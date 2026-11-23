"""The proof tables' grants and the live control that requires their triggers (SPEC_DECISIONS 68, item 5).

Every insert-only evidence table, the quarantine table of SPEC_DECISIONS 57 included, gives
the app role SELECT and INSERT and nothing else, and `EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED`
is true only while the statement level trigger of every one of them is enabled: disabling
any one turns that control, and only that control, false.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Engine

from bank_testkit.database import BankDatabase
from onepledge_bank_agent.domain.integrity import DatabaseControl
from onepledge_bank_agent.infrastructure.db.controls import INSERT_ONLY_EVIDENCE, SqlDatabaseControlInspector
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.migrations import head_revision

pytestmark = pytest.mark.integration

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
PROOF_TABLES = (*INSERT_ONLY_EVIDENCE, "loans_raw", "audit_log")


@pytest.fixture
def app_engine(bank_db: BankDatabase) -> Iterator[Engine]:
    engine = make_engine(bank_db.app_url)
    yield engine
    engine.dispose()


def granted(database: BankDatabase, table: str) -> set[str]:
    """The table privileges the app role holds on a table."""
    with database.connect("admin") as connection:
        return {
            privilege
            for privilege in PRIVILEGES
            if connection.execute(
                "SELECT has_table_privilege(%s, %s, %s)", (database.app_role, f"public.{table}", privilege)
            ).fetchone()
            == (True,)
        }


def test_the_quarantine_table_is_one_of_the_evidence_tables_the_control_reads() -> None:
    assert "registry_messages_quarantined" in INSERT_ONLY_EVIDENCE
    assert {"certificates", "checkpoints_seen", "integrity_runs"} <= set(INSERT_ONLY_EVIDENCE)


@pytest.mark.parametrize("table", PROOF_TABLES)
def test_the_app_role_may_only_read_and_append_each_proof_table(bank_db: BankDatabase, table: str) -> None:
    assert granted(bank_db, table) == {"SELECT", "INSERT"}


@pytest.mark.parametrize("table", INSERT_ONLY_EVIDENCE)
def test_disabling_any_evidence_trigger_turns_only_the_evidence_control_false(
    bank_db: BankDatabase, app_engine: Engine, table: str
) -> None:
    inspector = SqlDatabaseControlInspector(app_engine, head_revision())
    assert all(inspector.controls().values()), "every control holds on a fresh database"
    trigger = sql.Identifier(f"{table}_insert_only")
    with bank_db.connect("owner") as connection:
        connection.execute(
            sql.SQL("ALTER TABLE {} DISABLE TRIGGER {}").format(sql.Identifier(table), trigger)
        )
    broken = inspector.controls()
    assert [control for control, holds in broken.items() if not holds] == [
        DatabaseControl.EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED
    ]
    with bank_db.connect("owner") as connection:
        connection.execute(sql.SQL("ALTER TABLE {} ENABLE TRIGGER {}").format(sql.Identifier(table), trigger))
        connection.execute(sql.SQL("DROP TRIGGER {} ON {}").format(trigger, sql.Identifier(table)))
    assert inspector.controls()[DatabaseControl.EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED] is False, (
        "a missing trigger is not in force either"
    )


def test_the_quarantine_trigger_refuses_truncate_for_the_owner(bank_db: BankDatabase) -> None:
    with bank_db.connect("owner") as connection:
        connection.execute(
            "INSERT INTO registry_messages_quarantined VALUES "
            "(gen_random_uuid(), gen_random_uuid(), 1, 'ALERT', NULL, %s, 'OVERSIZED', now(), true)",
            ("e" * 64,),
        )
        triggers = connection.execute(
            "SELECT tgtype FROM pg_trigger WHERE tgname = 'registry_messages_quarantined_insert_only'"
        ).fetchall()
    truncate_bit, statement_level_bit = 32, 1
    assert len(triggers) == 1
    (kind,) = triggers[0]
    assert isinstance(kind, int)
    assert kind & truncate_bit and not kind & statement_level_bit, "TRUNCATE, for each statement"
    with bank_db.connect("owner") as connection:
        connection.autocommit = False
        with pytest.raises(psycopg.Error, match="insert-only") as raised:
            connection.execute("TRUNCATE registry_messages_quarantined")
        connection.rollback()
    assert raised.value.sqlstate == "OP001"
    with bank_db.connect("admin") as connection:
        assert connection.execute("SELECT count(*) FROM registry_messages_quarantined").fetchone() == (1,)
