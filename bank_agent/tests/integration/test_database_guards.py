"""Integrity enforced by PostgreSQL itself (SPEC 12.2, 12.4): privileges, insert-only triggers, state guards.

One bank database is filled by running the 20 loan example through intake, cleaning
and checks; every statement below is then tried as the app role and as the owner.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from bank_testkit.data import EXAMPLE_TAPES, seed_reference
from bank_testkit.database import BankDatabase, drop, new_bank_database, provision_and_migrate
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness
from onepledge_bank_agent.domain.enums import TapeKind

pytestmark = pytest.mark.integration

INSERT_ONLY = {
    "loans_raw": "row_number", "audit_log": "action", "cleaning_issues": "field",
    "submission_items": "row_number", "local_checks": "result", "loan_tokens": "token",
    "certificates": "kind", "checkpoints_seen": "signature", "integrity_runs": "status",
}  # fmt: skip
REFERENCED_BY_FOREIGN_KEYS = {"loans_raw"}


@pytest.fixture(scope="module")
def filled(
    tmp_path_factory: pytest.TempPathFactory, example_data: Path, example_outside: InProcessOutsideRecords
) -> Iterator[BankDatabase]:
    database = new_bank_database()
    provision_and_migrate(database)
    seed_reference(database, example_data)
    with BankAgentHarness(database, example_outside) as bank:
        bank.ingest(
            EXAMPLE_TAPES / "example_tape_20.csv", "HARBOR-SUMMIT", TapeKind.PLEDGE, date(2026, 10, 15)
        )
        bank.run_worker()
    with database.connect("owner") as connection:
        loan_id = connection.execute(
            "SELECT loan_id FROM loans WHERE state = 'PENDING' ORDER BY loan_id LIMIT 1"
        ).fetchone()
        assert loan_id is not None
        connection.execute(
            "INSERT INTO loan_tokens VALUES (%s, 'ASSET', '1.1', %s, now())", (loan_id[0], "c" * 64)
        )
    yield database
    drop(database)


def sqlstate(
    database: BankDatabase, role: str, statement: str | sql.Composed, parameters: tuple[object, ...] = ()
) -> str | None:
    """The SQLSTATE a statement fails with, or None when it succeeds (it is rolled back either way)."""
    with database.connect(role) as connection:
        connection.autocommit = False
        try:
            connection.execute(statement, parameters)
        except psycopg.Error as error:
            return error.sqlstate
        finally:
            connection.rollback()
    return None


def counts(database: BankDatabase) -> dict[str, object]:
    with database.connect("admin") as connection:
        count = sql.SQL("SELECT count(*) FROM {}")
        return {
            table: connection.execute(count.format(sql.Identifier(table))).fetchone() for table in INSERT_ONLY
        }


class Update:
    """`UPDATE <table> SET <change> WHERE <key> = %s` for a literal change written in the test."""

    def __init__(self, table: str, key: str) -> None:
        self._table, self._key = sql.Identifier(table), sql.Identifier(key)

    def format(self, change: str) -> sql.Composed:
        return sql.SQL("UPDATE {} SET {} WHERE {} = %s").format(self._table, sql.SQL(change), self._key)


@pytest.mark.parametrize("table", sorted(INSERT_ONLY))
def test_insert_only_tables_refuse_every_change_for_the_app_role_and_the_owner(
    filled: BankDatabase, table: str
) -> None:
    before = counts(filled)
    name, column = sql.Identifier(table), sql.Identifier(INSERT_ONLY[table])
    statements = {
        "UPDATE": sql.SQL("UPDATE {} SET {} = {}").format(name, column, column),
        "DELETE": sql.SQL("DELETE FROM {}").format(name),
        "TRUNCATE": sql.SQL("TRUNCATE {}").format(name),
    }
    for verb, statement in statements.items():
        assert sqlstate(filled, "app", statement) == "42501", verb
        owner = sqlstate(filled, "owner", statement)
        if verb == "TRUNCATE" and table in REFERENCED_BY_FOREIGN_KEYS:
            # PostgreSQL refuses to truncate a table other tables reference before any trigger runs.
            assert owner == "0A000", verb
        else:
            assert owner == "OP001", verb
    assert counts(filled) == before
    assert before["loans_raw"] == (20,)


def test_the_insert_only_trigger_names_the_table(filled: BankDatabase) -> None:
    with filled.connect("owner") as connection, pytest.raises(psycopg.Error) as raised:
        connection.execute("DELETE FROM audit_log WHERE false")
    assert raised.value.sqlstate == "OP001" and "table audit_log is insert-only" in str(raised.value)


def loan_with(filled: BankDatabase, condition: str) -> uuid.UUID:
    query = sql.SQL("SELECT loan_id FROM loans WHERE {} ORDER BY loan_id LIMIT 1").format(sql.SQL(condition))
    with filled.connect("admin") as connection:
        row = connection.execute(query).fetchone()
    assert row is not None
    value = row[0]
    assert isinstance(value, uuid.UUID)
    return value


def released_loan(filled: BankDatabase) -> uuid.UUID:
    """A pending loan without tokens moved PENDING to LOCKED to RELEASED (legal steps, as the owner)."""
    loan = loan_with(filled, "state = 'PENDING' AND loan_id NOT IN (SELECT loan_id FROM loan_tokens)")
    with filled.connect("owner") as connection:
        connection.execute("UPDATE loans SET state = 'LOCKED' WHERE loan_id = %s", (loan,))
        connection.execute("UPDATE loans SET state = 'RELEASED' WHERE loan_id = %s", (loan,))
    return loan


def test_the_loan_guard_allows_only_the_spec_transitions(filled: BankDatabase) -> None:
    tokenised = loan_with(filled, "loan_id IN (SELECT loan_id FROM loan_tokens)")
    held = loan_with(filled, "state = 'HELD_LOCAL'")
    plain = loan_with(filled, "state = 'PENDING' AND loan_id NOT IN (SELECT loan_id FROM loan_tokens)")
    update = Update("loans", "loan_id")
    assert sqlstate(filled, "owner", update.format("state = 'RELEASED'"), (plain,)) == "OP002"
    assert sqlstate(filled, "owner", update.format("state = 'LOCKED'"), (held,)) == "OP002"
    assert sqlstate(filled, "owner", update.format("state = 'CONFLICT'"), (held,)) == "OP002"
    assert sqlstate(filled, "app", update.format("state = 'LOCKED'"), (plain,)) is None
    assert sqlstate(filled, "app", update.format("state = 'PENDING'"), (held,)) is None
    raise_amount = (
        "original_amount_cents = original_amount_cents + 1, current_balance_cents = current_balance_cents"
    )
    assert sqlstate(filled, "owner", update.format(raise_amount), (tokenised,)) == "OP003"
    assert sqlstate(filled, "owner", update.format("vin = vin"), (tokenised,)) is None
    assert sqlstate(filled, "owner", update.format(raise_amount), (plain,)) is None
    assert sqlstate(filled, "owner", update.format("loan_id = gen_random_uuid()"), (plain,)) == "OP003"
    other = "first_submission_id = (SELECT submission_id FROM tape_submissions LIMIT 1)"
    assert sqlstate(filled, "owner", update.format(other), (plain,)) is None
    released = released_loan(filled)
    assert sqlstate(filled, "owner", update.format("state = 'LOCKED'"), (released,)) == "OP002"
    assert sqlstate(filled, "owner", update.format("state = 'PENDING'"), (released,)) == "OP002"


def test_only_one_live_row_per_facility_and_vin(filled: BankDatabase) -> None:
    copy = (
        "INSERT INTO loans SELECT gen_random_uuid(), facility_id, first_submission_id, lender_loan_number, "
        "vin, borrower_ref, origination_date, original_amount_cents, current_balance_cents, 'PENDING', "
        "now(), now() FROM loans WHERE loan_id = %s"
    )
    live = loan_with(filled, "state = 'HELD_LOCAL'")
    released = released_loan(filled)
    assert sqlstate(filled, "owner", copy, (live,)) == "23505"
    assert sqlstate(filled, "owner", copy, (released,)) is None


def test_the_tape_guard_allows_only_the_spec_transitions_and_keeps_upload_facts(filled: BankDatabase) -> None:
    with filled.connect("admin") as connection:
        row = connection.execute("SELECT submission_id, status FROM tape_submissions").fetchone()
    assert row is not None and row[1] == "CHECKED"
    tape = row[0]
    update = Update("tape_submissions", "submission_id")
    for change in ("status = 'RECEIVED'", "status = 'COMPLETED', completed_at = now()", "status = 'CLEANED'"):
        assert sqlstate(filled, "owner", update.format(change), (tape,)) == "OP004", change
    for change in (
        "file_sha256 = repeat('0', 64)",
        "row_count = row_count + 1",
        "kind = 'BACK_BOOK'",
        "uploaded_at = now()",
    ):
        assert sqlstate(filled, "owner", update.format(change), (tape,)) == "OP004", change
    fail = (
        "status = 'FAILED', resume_status = 'CHECKED', failed_stage = 'TOKENIZED', "
        "failure_code = 'INTERNAL_ERROR'"
    )
    with filled.connect("app") as connection:
        connection.execute(update.format(fail), (tape,))
        connection.execute(update.format("attempts = 3"), (tape,))
    wrong_resume = "status = 'CLEANED', resume_status = NULL, failed_stage = NULL, failure_code = NULL"
    assert sqlstate(filled, "owner", update.format(wrong_resume), (tape,)) == "OP004"
    resume = (
        "status = 'CHECKED', resume_status = NULL, failed_stage = NULL, failure_code = NULL, attempts = 0"
    )
    assert sqlstate(filled, "app", update.format(resume), (tape,)) is None
    with filled.connect("app") as connection:
        connection.execute(update.format(resume), (tape,))


def test_the_audit_chain_guard_refuses_a_gap_or_a_wrong_link(filled: BankDatabase) -> None:
    with filled.connect("admin") as connection:
        last = connection.execute(
            "SELECT seq, entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    assert last is not None
    insert = (
        "INSERT INTO audit_log (audit_id, seq, at, actor_type, action, object_type, outcome, detail, "
        "prev_hash, entry_hash) VALUES (gen_random_uuid(), %s, now(), 'SYSTEM', 'TAPE_FAILED', "
        "'TAPE_SUBMISSION', 'FAILURE', '{}', %s, repeat('e', 64))"
    )
    seq, entry_hash = int(str(last[0])), str(last[1])
    assert sqlstate(filled, "owner", insert, (seq + 2, entry_hash)) == "OP005"
    assert sqlstate(filled, "owner", insert, (seq + 1, "f" * 64)) == "OP005"
    assert sqlstate(filled, "app", insert, (seq + 1, "f" * 64)) == "OP005"
    assert sqlstate(filled, "owner", insert, (seq + 1, entry_hash)) is None


def test_the_app_role_has_least_privilege(
    filled: BankDatabase, new_database: Callable[[], BankDatabase]
) -> None:
    for statement in (
        "CREATE TABLE x (i int)",
        "DROP TABLE loans",
        "DELETE FROM loans",
        "DELETE FROM tape_submissions",
        "ALTER TABLE loans_raw DISABLE TRIGGER ALL",
        "UPDATE facilities SET name = name",
        "INSERT INTO lenders VALUES (gen_random_uuid(), 'x', '00000ONEPLEDGESUM134', now())",
        "UPDATE alembic_version SET version_num = version_num",
    ):
        assert sqlstate(filled, "app", statement) == "42501", statement
    with filled.connect("admin") as connection:
        roles = connection.execute(
            "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles "
            "WHERE rolname LIKE %s ORDER BY 1",
            (filled.name + "%",),
        ).fetchall()
    assert roles == [(filled.app_role, False, False, False), (filled.target.owner_role, False, False, False)]
    other = new_database()
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(filled.app_url.rsplit("/", 1)[0] + "/" + other.name).close()
