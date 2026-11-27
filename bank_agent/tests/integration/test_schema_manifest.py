"""The schema is compared against a manifest, not sampled (SPEC_DECISIONS 105; review 3 quality 2).

A fresh database migrated to head has exactly the catalogue of `schema_manifest.json`, so the
file cannot drift from the migrations: a migration that changes the schema changes the file in
the same commit. Then each change of the decision, made as a superuser, turns
`SCHEMA_MATCHES_MANIFEST` false and is named in the report: a dropped check constraint, an
added rule, an added trigger, a changed column type, and a row security policy.

The catalogue reads the same whatever the server's time zone (SPEC_DECISIONS 137): a database
whose default `TimeZone` is not UTC, and a session set to another time zone, both give exactly
the manifest, because the bank agent's connections run in UTC and `catalogue()` renders in UTC.
"""

from __future__ import annotations

from typing import LiteralString

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Connection, text

from bank_testkit.database import BankDatabase
from bank_testkit.schema_manifest_file import manifest_path, render
from onepledge_bank_agent.domain.integrity import ControlViolation, DatabaseControl
from onepledge_bank_agent.infrastructure.db.controls import SqlDatabaseControlInspector
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.migrations import head_revision
from onepledge_bank_agent.infrastructure.db.schema_manifest import (
    CatalogueTimeZoneError,
    catalogue,
    differences,
    digest,
    expected,
)

pytestmark = pytest.mark.integration


def inspect(database: BankDatabase) -> tuple[bool, tuple[ControlViolation, ...]]:
    engine = make_engine(database.app_url)
    try:
        inspector = SqlDatabaseControlInspector(engine, head_revision())
        return inspector.controls()[DatabaseControl.SCHEMA_MATCHES_MANIFEST], inspector.violations()
    finally:
        engine.dispose()


def test_a_database_migrated_to_head_has_exactly_the_catalogue_of_the_manifest(bank_db: BankDatabase) -> None:
    engine = make_engine(bank_db.owner_url)
    try:
        with engine.connect() as connection:
            live = catalogue(connection)
    finally:
        engine.dispose()
    assert differences(live, expected()) == [] and digest(live) == digest(expected())
    assert manifest_path().read_text(encoding="utf-8") == render(live), "the file is in its canonical form"
    assert inspect(bank_db) == (True, ())


# Not UTC, with a half hour offset and no daylight saving time, unlike the dev server's New York.
OTHER_ZONE = "Asia/Kolkata"


def _on_owner_engine(database: BankDatabase) -> tuple[str, dict[str, str]]:
    engine = make_engine(database.owner_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SHOW TimeZone")).scalar_one(), catalogue(connection)
    finally:
        engine.dispose()


def test_the_catalogue_is_the_manifest_on_a_database_whose_default_time_zone_is_not_utc(
    bank_db: BankDatabase,
) -> None:
    """A database default is allowed for the app role (decision 103 forbids role settings), so a
    server or database in another time zone must not change what the bank agent reads."""
    with bank_db.connect("admin") as admin:
        statement = sql.SQL("ALTER DATABASE {} SET TimeZone = {}")
        admin.execute(statement.format(sql.Identifier(bank_db.name), sql.Literal(OTHER_ZONE)))
    with psycopg.connect(bank_db.owner_url) as plain:
        assert plain.execute("SHOW TimeZone").fetchone() == (OTHER_ZONE,), "the default is in force"
    zone, live = _on_owner_engine(bank_db)
    assert zone == "UTC"
    assert differences(live, expected()) == []
    assert inspect(bank_db) == (True, ())


def test_the_catalogue_renders_in_utc_whatever_the_session_time_zone(bank_db: BankDatabase) -> None:
    engine = make_engine(bank_db.owner_url)
    try:
        with engine.connect() as connection:
            connection.execute(text(f"SET TimeZone = '{OTHER_ZONE}'"))
            connection.commit()  # a session setting now, which a rollback does not undo
            live = catalogue(connection)
            connection.rollback()
            assert connection.execute(text("SHOW TimeZone")).scalar_one() == OTHER_ZONE, "left as it was"
    finally:
        engine.dispose()
    assert differences(live, expected()) == []
    constraint = "constraint certificates.ck_certificates_issued_at_range"
    assert "'2000-01-01 00:00:00+00'::timestamp with time zone" in live[constraint]


def test_a_catalogue_read_outside_a_transaction_in_another_time_zone_is_refused(
    bank_db: BankDatabase,
) -> None:
    """In autocommit mode the setting would last one statement; the catalogue says so, never a wrong text."""
    engine = make_engine(bank_db.owner_url)
    try:
        with engine.connect() as connection:
            autocommit: Connection = connection.execution_options(isolation_level="AUTOCOMMIT")
            autocommit.execute(text(f"SET TimeZone = '{OTHER_ZONE}'"))
            with pytest.raises(CatalogueTimeZoneError):
                catalogue(autocommit)
    finally:
        engine.dispose()


CHANGES: list[tuple[LiteralString, str, str]] = [
    ("ALTER TABLE loans DROP CONSTRAINT ck_loans_lender_loan_number", "SCHEMA_OBJECT_MISSING",
     "constraint loans.ck_loans_lender_loan_number"),
    ("CREATE RULE optest_quiet AS ON DELETE TO alerts DO INSTEAD NOTHING", "SCHEMA_OBJECT_ADDED",
     "rule alerts.optest_quiet"),
    ("CREATE TRIGGER optest_extra BEFORE UPDATE ON loans FOR EACH ROW "
     "EXECUTE FUNCTION onepledge_loans_guard()", "SCHEMA_OBJECT_ADDED", "trigger loans.optest_extra"),
    ("ALTER TABLE alerts ALTER COLUMN note TYPE varchar(10)", "SCHEMA_OBJECT_CHANGED", "column alerts.note"),
    ("CREATE POLICY optest_all ON loans USING (true)", "SCHEMA_OBJECT_ADDED", "policy loans.optest_all"),
    ("ALTER TABLE loans ENABLE ROW LEVEL SECURITY", "SCHEMA_OBJECT_CHANGED", "table loans"),
    ('ALTER TABLE facilities ALTER COLUMN name TYPE text COLLATE "und-x-icu"', "SCHEMA_OBJECT_CHANGED",
     "column facilities.name"),
]  # fmt: skip


@pytest.mark.parametrize(("statement", "code", "name"), CHANGES, ids=[c[2] for c in CHANGES])
def test_each_change_of_the_schema_turns_the_control_false_and_is_named(
    bank_db: BankDatabase, statement: LiteralString, code: str, name: str
) -> None:
    with bank_db.connect("admin") as connection:
        connection.execute(statement)
    holds, violations = inspect(bank_db)
    assert not holds
    manifest = DatabaseControl.SCHEMA_MATCHES_MANIFEST
    assert ControlViolation(manifest, code, name) in violations
