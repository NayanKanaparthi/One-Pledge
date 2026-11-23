"""Controls prove the guard is the one that was shipped (SPEC_DECISIONS 90; review 2, quality finding 3).

The manifest equals what the migrations install, so it cannot drift. Then the three
replacements of decision 90, made by the owner, each turn the matching controls false and
leave every other control true: `onepledge_forbid_mutation()` replaced by a function that
returns NULL, `onepledge_loans_guard()` replaced by `RETURN NEW`, and
`audit_log_insert_only` recreated for TRUNCATE only.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from bank_testkit.database import BankDatabase
from onepledge_bank_agent.domain.integrity import DatabaseControl
from onepledge_bank_agent.infrastructure.db.controls import SqlDatabaseControlInspector
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.guard_manifest import MANIFEST, installed_triggers
from onepledge_bank_agent.infrastructure.db.migrations import head_revision

pytestmark = pytest.mark.integration


@pytest.fixture
def inspector(bank_db: BankDatabase) -> Iterator[SqlDatabaseControlInspector]:
    engine: Engine = make_engine(bank_db.app_url)
    yield SqlDatabaseControlInspector(engine, head_revision())
    engine.dispose()


def false_controls(inspector: SqlDatabaseControlInspector) -> set[DatabaseControl]:
    return {control for control, holds in inspector.controls().items() if not holds}


def test_the_manifest_equals_the_triggers_a_fresh_database_has_at_head(bank_db: BankDatabase) -> None:
    engine = make_engine(bank_db.app_url)
    try:
        with engine.connect() as connection:
            installed = installed_triggers(connection)
    finally:
        engine.dispose()
    assert {key: found.guard for key, found in installed.items()} == dict(MANIFEST)
    assert {(found.enabled, found.function_schema) for found in installed.values()} == {("O", "public")}


REPLACEMENTS = {
    "forbid mutation returns NULL": (
        "CREATE OR REPLACE FUNCTION onepledge_forbid_mutation() RETURNS trigger LANGUAGE plpgsql "
        "AS $$ BEGIN RETURN NULL; END $$",
        {
            DatabaseControl.LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED,
            DatabaseControl.AUDIT_LOG_IMMUTABILITY_TRIGGER_ENABLED,
            DatabaseControl.EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED,
        },
    ),
    "loans guard returns NEW": (
        "CREATE OR REPLACE FUNCTION onepledge_loans_guard() RETURNS trigger LANGUAGE plpgsql "
        "AS $$ BEGIN RETURN NEW; END $$",
        {DatabaseControl.LOAN_STATE_GUARD_TRIGGER_ENABLED},
    ),
    "audit trigger for TRUNCATE only": (
        "DROP TRIGGER audit_log_insert_only ON audit_log; "
        "CREATE TRIGGER audit_log_insert_only BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION onepledge_forbid_mutation()",
        {DatabaseControl.AUDIT_LOG_IMMUTABILITY_TRIGGER_ENABLED},
    ),
    "alerts guard moved to another schema's function": (
        "CREATE SCHEMA shadow; CREATE FUNCTION shadow.onepledge_alerts_guard() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$; DROP TRIGGER alerts_guard ON alerts; "
        "CREATE TRIGGER alerts_guard BEFORE INSERT OR UPDATE ON alerts FOR EACH ROW "
        "EXECUTE FUNCTION shadow.onepledge_alerts_guard()",
        # the new function is executable by PUBLIC, which decision 103 point 8 refuses as well
        {DatabaseControl.ALERT_STATE_GUARD_TRIGGER_ENABLED, DatabaseControl.APP_ROLE_GRANTS_EXACT},
    ),
}


@pytest.mark.parametrize("name", sorted(REPLACEMENTS))
def test_a_replaced_or_narrowed_guard_turns_only_its_controls_false(
    bank_db: BankDatabase, inspector: SqlDatabaseControlInspector, name: str
) -> None:
    statement, expected = REPLACEMENTS[name]
    assert false_controls(inspector) == set(), "every control holds on a fresh database"
    with bank_db.connect("admin") as connection:
        connection.execute(statement)
    assert false_controls(inspector) == {*expected, DatabaseControl.SCHEMA_MATCHES_MANIFEST}, (
        "its own controls, and the schema manifest (SPEC_DECISIONS 105)"
    )
