"""Provisioning, migrations, privileges, seeding and client storage on a real PostgreSQL server."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from psycopg import errors as pg_errors
from psycopg import sql
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from onepledge_outside_records.application.administration import RegisterClient, SeedRecords
from onepledge_outside_records.application.lookups import AuthenticateClient
from onepledge_outside_records.domain.errors import OperatorError, RecordsRefusedError, UnauthenticatedError
from onepledge_outside_records.infrastructure.csv_source import CsvRecordsSource
from onepledge_outside_records.infrastructure.db.engines import make_engine
from onepledge_outside_records.infrastructure.db.migrations import MigrationRunner
from onepledge_outside_records.infrastructure.db.models import Base
from onepledge_outside_records.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_outside_records.infrastructure.db.repositories import (
    SqlClientRegistry,
    SqlRecordsReader,
    SqlRecordsWriter,
)

if TYPE_CHECKING:
    from conftest import Database

pytestmark = pytest.mark.integration

TABLES = ("title_records", "payments", "contracts", "api_clients")
UNREACHABLE = "postgresql://nobody:pw@127.0.0.1:1/none"


def table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def contents(engine: Engine) -> dict[str, str]:
    """A digest of each data table's full contents in primary key order."""
    statements = {
        "title_records": "SELECT t::text FROM title_records t ORDER BY vin",
        "payments": "SELECT t::text FROM payments t ORDER BY payment_id",
        "contracts": "SELECT t::text FROM contracts t ORDER BY contract_id",
    }
    digests = {}
    with engine.connect() as connection:
        for table, statement in statements.items():
            rows: list[str] = list(connection.execute(text(statement)).scalars().all())
            digests[table] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
    return digests


def test_provisioning_twice_keeps_the_database_and_resets_nothing_else(
    provisioned_database: Database,
) -> None:
    provisioner = DatabaseProvisioner(provisioned_database.admin_url)
    provisioner.provision(provisioned_database.target)
    with psycopg.connect(provisioned_database.admin_url) as connection:
        owner = connection.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s",
            (provisioned_database.target.database,),
        ).fetchone()
        roles = connection.execute(
            "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname LIKE %s",
            (provisioned_database.target.database + "%",),
        ).fetchall()
    assert owner == (provisioned_database.target.owner_role,)
    assert sorted(roles) == [
        (provisioned_database.target.app_role, False, False, False),
        (provisioned_database.target.owner_role, False, False, False),
    ]


def test_migrations_go_up_down_and_up_again(provisioned_database: Database) -> None:
    runner = MigrationRunner(provisioned_database.owner_url, provisioned_database.app_role)
    engine = make_engine(provisioned_database.owner_url)
    try:
        runner.upgrade()
        assert set(TABLES) <= table_names(engine)
        runner.downgrade()
        assert table_names(engine) == {"alembic_version"}
        runner.upgrade()
        assert set(TABLES) <= table_names(engine)
        with engine.connect() as connection:
            version: str = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == runner.head_revision() == "0003_contract_checks"
    finally:
        engine.dispose()


def test_the_models_equal_the_migrated_schema(database: Database, owner_engine: Engine) -> None:
    with owner_engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert differences == []


def test_the_payment_index_orders_paid_on_descending(owner_engine: Engine) -> None:
    with owner_engine.connect() as connection:
        definition: str = connection.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_payments_vin_paid_on'")
        ).scalar_one()
    assert "(vin, paid_on DESC, payment_id)" in definition


def test_migrating_needs_an_existing_app_role(provisioned_database: Database) -> None:
    drop_app_role(provisioned_database)
    with pytest.raises(OperatorError, match="does not exist"):
        MigrationRunner(provisioned_database.owner_url, provisioned_database.app_role).upgrade()


def drop_app_role(database: Database) -> None:
    """Drop the provisioned app role, so the grants migration finds no role of the right name."""
    role = sql.Identifier(database.app_role)
    with psycopg.connect(database.admin_url, autocommit=True, dbname=database.target.database) as connection:
        connection.execute(sql.SQL("DROP OWNED BY {}").format(role))
    with psycopg.connect(database.admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("DROP OWNED BY {}").format(role))
        connection.execute(sql.SQL("DROP ROLE {}").format(role))


def test_migrating_an_unprovisioned_database_says_what_to_run(fresh_database: Database) -> None:
    with pytest.raises(OperatorError) as raised:
        MigrationRunner(fresh_database.owner_url, fresh_database.app_role).upgrade()
    assert raised.value.sentence == (
        "PostgreSQL refused the connection of MIGRATION_DATABASE_URL: its role, password or database "
        "is not set up; run db provision, or fix the URL."
    )


def test_provisioning_needs_a_reachable_server(fresh_database: Database) -> None:
    with pytest.raises(OperatorError, match="not reachable"):
        DatabaseProvisioner(UNREACHABLE).provision(fresh_database.target)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO title_records VALUES ('1HGCM82633A004352', 'NJ', 'CLEAN', NULL, NULL)",
        "UPDATE payments SET amount_cents = 1",
        "DELETE FROM contracts",
        "TRUNCATE title_records",
        "INSERT INTO api_clients (client_id, name, api_key_sha256) VALUES (gen_random_uuid(), 'x', "
        "repeat('a', 64))",
        "UPDATE api_clients SET active = true",
        "CREATE TABLE intruder (id int)",
    ],
)
def test_the_app_role_cannot_write_anything(database: Database, api_key: str, statement: str) -> None:
    with psycopg.connect(database.app_url) as connection, pytest.raises(pg_errors.InsufficientPrivilege):
        connection.execute(statement)


def test_the_app_role_can_read_the_four_tables(database: Database, api_key: str) -> None:
    with psycopg.connect(database.app_url) as connection:
        for table in TABLES:
            connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()


def test_seeding_counts_rows_and_is_idempotent(
    owner_engine: Engine, example_dir: Path, example_records: Path, api_key: str
) -> None:
    before = contents(owner_engine)
    counts = SeedRecords(CsvRecordsSource(), SqlRecordsWriter(owner_engine)).run(example_dir)
    lines = {
        name: len((example_records / name).read_text().splitlines()) - 1
        for name in ("titles.csv", "payments.csv", "contracts.csv")
    }
    assert (counts.titles, counts.payments, counts.contracts) == (
        lines["titles.csv"],
        lines["payments.csv"],
        lines["contracts.csv"],
    )
    assert contents(owner_engine) == before


def test_a_failed_seed_changes_nothing(
    owner_engine: Engine, tmp_path: Path, copy_records: Callable[[Path], Path], api_key: str
) -> None:
    before = contents(owner_engine)
    directory = copy_records(tmp_path)
    # A title status outside the contract: the file is well formed, the record rules refuse it.
    titles = directory / "outside_records" / "titles.csv"
    lines = titles.read_text().splitlines()
    cells = lines[1].split(",")
    cells[2] = "WRECKED"
    titles.write_text("\n".join([lines[0], ",".join(cells), *lines[2:]]) + "\n")
    with pytest.raises(RecordsRefusedError) as raised:
        SeedRecords(CsvRecordsSource(), SqlRecordsWriter(owner_engine)).run(directory)
    assert raised.value.sentence == "The outside records hold 1 problem; nothing was loaded."
    assert raised.value.problems == (
        "titles.csv line 2, column title_status: must be one of CLEAN, SALVAGE, JUNK",
    )
    assert contents(owner_engine) == before


def test_seeding_before_migrating_says_what_to_run(provisioned_database: Database, example_dir: Path) -> None:
    engine = make_engine(provisioned_database.owner_url)
    try:
        with pytest.raises(OperatorError, match="run db migrate first"):
            SeedRecords(CsvRecordsSource(), SqlRecordsWriter(engine)).run(example_dir)
    finally:
        engine.dispose()


def test_seeding_an_unreachable_server_says_what_to_run(example_dir: Path) -> None:
    engine = make_engine(UNREACHABLE)
    with pytest.raises(OperatorError) as raised:
        SeedRecords(CsvRecordsSource(), SqlRecordsWriter(engine)).run(example_dir)
    assert raised.value.sentence == (
        "PostgreSQL is not reachable at MIGRATION_DATABASE_URL; start it or fix the URL."
    )


def test_clients_are_stored_by_hash_and_a_new_key_replaces_the_old(
    owner_engine: Engine, app_engine: Engine, make_key: Callable[[], str]
) -> None:
    registry, authenticate = SqlClientRegistry(owner_engine), AuthenticateClient(SqlRecordsReader(app_engine))
    first, second = make_key(), make_key()
    created = RegisterClient(registry).run("northgate-test", first)
    assert authenticate.run(first).client_id == created.client_id
    with owner_engine.connect() as connection:
        stored: str = connection.execute(
            text("SELECT t::text FROM api_clients t WHERE name = 'northgate-test'")
        ).scalar_one()
    assert first not in stored
    assert hashlib.sha256(first.encode()).hexdigest() in stored
    replaced = RegisterClient(registry).run("northgate-test", second)
    assert replaced.client_id == created.client_id
    assert authenticate.run(second).name == "northgate-test"
    with pytest.raises(UnauthenticatedError):
        authenticate.run(first)


def test_an_inactive_client_cannot_authenticate(
    owner_engine: Engine, app_engine: Engine, make_key: Callable[[], str]
) -> None:
    key = make_key()
    RegisterClient(SqlClientRegistry(owner_engine)).run("calder-test", key)
    with owner_engine.begin() as connection:
        connection.execute(text("UPDATE api_clients SET active = false WHERE name = 'calder-test'"))
    with pytest.raises(UnauthenticatedError):
        AuthenticateClient(SqlRecordsReader(app_engine)).run(key)
    RegisterClient(SqlClientRegistry(owner_engine)).run("calder-test", key)
    assert AuthenticateClient(SqlRecordsReader(app_engine)).run(key).name == "calder-test"


def test_the_roles_of_one_database_cannot_connect_to_another(
    database: Database, provisioned_database: Database
) -> None:
    for own_url in (provisioned_database.app_url, provisioned_database.owner_url):
        foreign = own_url.rsplit("/", 1)[0] + "/" + database.target.database
        with pytest.raises(psycopg.OperationalError, match="permission denied"):
            psycopg.connect(foreign).close()


def test_an_unknown_revision_is_one_plain_sentence(provisioned_database: Database) -> None:
    runner = MigrationRunner(provisioned_database.owner_url, provisioned_database.app_role)
    with pytest.raises(OperatorError) as raised:
        runner.upgrade("9999_unknown")
    assert raised.value.sentence.startswith("Alembic could not run the migrations")
    assert not any(mark in raised.value.sentence for mark in ("'", "[", "("))


# SPEC_DECISIONS 109.5: the database holds the contract's rules too, whoever writes the row.
VIN = "1HGCM82633A004352"
UUID = "0b7c2c1e-4f7d-4c1e-9a52-6a1f2d3c4b5a"
DAY = date(2026, 1, 1)
INSERTS = {
    "title_records": text("INSERT INTO title_records VALUES (:vin, :state, :status, :name, :day)"),
    "payments": text("INSERT INTO payments VALUES (:id, :vin, :day, :cents, :bank)"),
    "contracts": text("INSERT INTO contracts VALUES (:id, :vin, :name, :cents, :day, :sha)"),
}
TITLE = {"vin": VIN, "state": "NJ", "status": "CLEAN", "name": None, "day": None}
PAYMENT = {"id": UUID, "vin": VIN, "day": DAY, "cents": 1, "bank": "HARBOR"}
CONTRACT = {"id": UUID, "vin": VIN, "name": "Summit", "cents": 1, "day": DAY, "sha": "a" * 64}
FORBIDDEN_ROWS: dict[str, tuple[str, dict[str, object]]] = {
    "title state": ("title_records", {**TITLE, "state": "nj"}),
    "title vin": ("title_records", {**TITLE, "vin": VIN.lower()}),
    "lienholder length": ("title_records", {**TITLE, "name": "B" * 201}),
    "lienholder control": ("title_records", {**TITLE, "name": "Harbor\x07"}),
    "lien date": ("title_records", {**TITLE, "day": date(1999, 12, 31)}),
    "payment vin": ("payments", {**PAYMENT, "vin": "SHORT"}),
    "payment date": ("payments", {**PAYMENT, "day": date(2100, 1, 1)}),
    "bank code": ("payments", {**PAYMENT, "bank": "harbor bank 1"}),
    "contract vin": ("contracts", {**CONTRACT, "vin": "SHORT"}),
    "lender length": ("contracts", {**CONTRACT, "name": "A" * 201}),
    "lender control": ("contracts", {**CONTRACT, "name": "A\x01"}),
    "contract date": ("contracts", {**CONTRACT, "day": date(1999, 1, 1)}),
}


@pytest.mark.parametrize(("table", "values"), FORBIDDEN_ROWS.values(), ids=list(FORBIDDEN_ROWS))
def test_the_database_refuses_a_row_the_contract_forbids(
    owner_engine: Engine, table: str, values: dict[str, object]
) -> None:
    with owner_engine.connect() as connection, pytest.raises(IntegrityError) as raised:
        connection.execute(INSERTS[table], values)
    assert isinstance(raised.value.orig, pg_errors.CheckViolation)


def test_the_valid_rows_of_those_cases_are_accepted(owner_engine: Engine) -> None:
    with owner_engine.connect() as connection:
        for table, values in (("title_records", TITLE), ("payments", PAYMENT), ("contracts", CONTRACT)):
            connection.execute(INSERTS[table], values)
        connection.rollback()


def test_a_key_another_client_already_has_is_refused_by_name(
    owner_engine: Engine, make_key: Callable[[], str]
) -> None:
    """Review 4 quality 10: this said a constraint failed."""
    registry, key = SqlClientRegistry(owner_engine), make_key()
    RegisterClient(registry).run("calder-test", key)
    with pytest.raises(OperatorError) as raised:
        RegisterClient(registry).run("lakeside-test", key)
    assert raised.value.sentence == "Another client already has this key; generate a new key for this client."
    assert raised.value.code == "INVALID_INPUT"
