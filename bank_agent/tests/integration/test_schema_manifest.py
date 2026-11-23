"""The schema is compared against a manifest, not sampled (SPEC_DECISIONS 105; review 3 quality 2).

A fresh database migrated to head has exactly the catalogue of `schema_manifest.json`, so the
file cannot drift from the migrations: a migration that changes the schema changes the file in
the same commit. Then each change of the decision, made as a superuser, turns
`SCHEMA_MATCHES_MANIFEST` false and is named in the report: a dropped check constraint, an
added rule, an added trigger, a changed column type, and a row security policy.
"""

from __future__ import annotations

from typing import LiteralString

import pytest

from bank_testkit.database import BankDatabase
from onepledge_bank_agent.domain.integrity import ControlViolation, DatabaseControl
from onepledge_bank_agent.infrastructure.db.controls import SqlDatabaseControlInspector
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.migrations import head_revision
from onepledge_bank_agent.infrastructure.db.schema_manifest import catalogue, differences, digest, expected

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
    assert inspect(bank_db) == (True, ())


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
