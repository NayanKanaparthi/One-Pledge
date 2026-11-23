"""The three lookups answer the same on a database whose default collation is not "C" (SPEC_DECISIONS 133).

The server has ICU, so the scratch database is created with the ICU default collation
`und` (`und-x-icu`), migrated, seeded with the example records and given the same client
key as the session database, whose default is `C.UTF-8`. Every VIN of the example records
gets the same three answers, byte for byte and in the same order, from both. A
case-insensitive, non-deterministic ICU collation cannot be a database default, so it is
created in the scratch database and applied where a test can apply it: a superuser moves
one column onto it, and the catalogue check names that column.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Engine

from onepledge_outside_records.application.administration import RegisterClient, SeedRecords
from onepledge_outside_records.infrastructure.csv_source import CsvRecordsSource
from onepledge_outside_records.infrastructure.db.engines import make_engine
from onepledge_outside_records.infrastructure.db.migrations import MigrationRunner
from onepledge_outside_records.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_outside_records.infrastructure.db.repositories import (
    SqlClientRegistry,
    SqlRecordsReader,
    SqlRecordsWriter,
)
from onepledge_outside_records.interface.http.app import create_app

if TYPE_CHECKING:
    from conftest import Database, InProcessApi

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

ICU_DEFAULT = "und"
CASE_INSENSITIVE = "optest_case_insensitive"


def collations(engine: Engine) -> dict[str, str]:
    """Every collatable column of the schema and its collation, from the catalogue."""
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT c.relname || '.' || a.attname, coll.collname FROM pg_attribute AS a "
            "JOIN pg_class AS c ON c.oid = a.attrelid JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "JOIN pg_collation AS coll ON coll.oid = a.attcollation "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped"
        )
        return {str(row[0]): str(row[1]) for row in rows}


@pytest.fixture
def icu_database(provisioned_database: Database) -> Database:
    """A provisioned database recreated with the ICU default collation `und`, then migrated."""
    target = provisioned_database.target
    with psycopg.connect(provisioned_database.admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(target.database)))
        admin.execute(
            sql.SQL(
                "CREATE DATABASE {} OWNER {} ENCODING 'UTF8' LOCALE_PROVIDER icu ICU_LOCALE {} "
                "LOCALE 'C.UTF-8' TEMPLATE template0"
            ).format(
                sql.Identifier(target.database), sql.Identifier(target.owner_role), sql.Literal(ICU_DEFAULT)
            )
        )
    DatabaseProvisioner(provisioned_database.admin_url).provision(target)
    MigrationRunner(provisioned_database.owner_url, provisioned_database.app_role).upgrade()
    return provisioned_database


@pytest.fixture
def icu_call(
    icu_database: Database, example_dir: Path, api_key: str, call_app: Callable[..., InProcessApi]
) -> Iterator[Callable[..., httpx.Response]]:
    """`call` against the app on the ICU database, seeded like the session database."""
    owner, app = make_engine(icu_database.owner_url), make_engine(icu_database.app_url, service=True)
    try:
        SeedRecords(CsvRecordsSource(), SqlRecordsWriter(owner)).run(example_dir)
        RegisterClient(SqlClientRegistry(owner)).run("harbor", api_key)
        yield call_app(create_app(SqlRecordsReader(app))).request
    finally:
        owner.dispose()
        app.dispose()


def _vins(example_records: Path) -> list[str]:
    vins: set[str] = set()
    for name in ("titles.csv", "payments.csv", "contracts.csv"):
        lines = (example_records / name).read_text(encoding="utf-8").splitlines()[1:]
        column = 0 if name == "titles.csv" else 1
        vins.update(line.split(",")[column] for line in lines)
    return sorted(vins)


def _answer(response: httpx.Response) -> bytes:
    """The body, with the request id of an error (new on every request) left out."""
    document = response.json()
    if isinstance(document, dict) and isinstance(document.get("error"), dict):
        document["error"].pop("request_id", None)
    return json.dumps(document).encode()


def test_the_icu_database_is_really_not_c(icu_database: Database) -> None:
    with psycopg.connect(icu_database.admin_url, dbname=icu_database.target.database) as admin:
        row = admin.execute(
            "SELECT datlocprovider, daticulocale FROM pg_database WHERE datname = current_database()"
        ).fetchone()
    assert row == ("i", ICU_DEFAULT)


def test_every_text_column_is_c_on_a_database_whose_default_is_icu(icu_database: Database) -> None:
    engine = make_engine(icu_database.owner_url)
    try:
        found = collations(engine)
    finally:
        engine.dispose()
    assert found and {column: name for column, name in found.items() if name != "C"} == {}


def test_the_three_lookups_answer_the_same_in_the_same_order_on_both_databases(
    call: Callable[..., httpx.Response],
    icu_call: Callable[..., httpx.Response],
    api_key: str,
    example_records: Path,
) -> None:
    headers = {"X-API-Key": api_key}
    vins = _vins(example_records)
    assert len(vins) > 10
    for vin in vins:
        for path in (f"/v1/titles/{vin}", f"/v1/payments?vin={vin}&limit=200", f"/v1/contracts/{vin}"):
            on_c, on_icu = call("GET", path, headers=headers), icu_call("GET", path, headers=headers)
            assert (on_icu.status_code, _answer(on_icu)) == (on_c.status_code, _answer(on_c)), path


def test_client_names_that_differ_only_in_case_are_two_clients(
    icu_database: Database, make_key: Callable[[], str]
) -> None:
    engine = make_engine(icu_database.owner_url)
    try:
        registry = RegisterClient(SqlClientRegistry(engine))
        lower, upper = registry.run("lakeside", make_key()), registry.run("LAKESIDE", make_key())
    finally:
        engine.dispose()
    assert lower.client_id != upper.client_id


def test_a_column_moved_to_a_case_insensitive_collation_is_named(icu_database: Database) -> None:
    with psycopg.connect(
        icu_database.admin_url, autocommit=True, dbname=icu_database.target.database
    ) as admin:
        admin.execute(
            sql.SQL(
                "CREATE COLLATION {} (provider = icu, locale = 'und-u-ks-level2', deterministic = false)"
            ).format(sql.Identifier(CASE_INSENSITIVE))
        )
        admin.execute(
            sql.SQL("ALTER TABLE api_clients ALTER COLUMN name TYPE text COLLATE {}").format(
                sql.Identifier(CASE_INSENSITIVE)
            )
        )
    engine = make_engine(icu_database.owner_url)
    try:
        found = collations(engine)
    finally:
        engine.dispose()
    assert {column: name for column, name in found.items() if name != "C"} == {
        "api_clients.name": CASE_INSENSITIVE
    }
