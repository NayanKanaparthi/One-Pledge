"""What may change on a guarded row, column by column (SPEC_DECISIONS 86, 87; review 2, F3, quality 6 and 10).

Every column of `loans`, `tape_submissions` and `alerts` is classified in
`infrastructure/db/guard_columns.py`, and the first test fails when the database has a
column that is not. Then each immutable and each conditional column is changed alone, as
the app role and as the owner, from every relevant row state, and the guard's SQLSTATE is
expected; a mutable column changes freely until the row is frozen. A clause deleted from a
guard fails one of these cases. The three statements of review F3 are refused, by the
guard and, with the guard switched off, by the check constraint.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from psycopg import sql

from bank_testkit.data import EXAMPLE_TAPES, seed_reference
from bank_testkit.database import BankDatabase, drop, new_bank_database, provision_and_migrate
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from bank_testkit.sql_rows import Action, attempt, new_alert, new_loan, new_tape, statement, values_insert
from onepledge_bank_agent.domain.enums import TapeKind, TapeStatus
from onepledge_bank_agent.infrastructure.db.guard_columns import (
    FROZEN_CODES,
    GUARD_CODES,
    GUARDED_COLUMNS,
    ColumnClass,
)

pytestmark = pytest.mark.integration

ROLES = ("app", "owner")
USER = "(SELECT user_id FROM users ORDER BY user_id LIMIT 1)"
OTHER_USER = "(SELECT u.user_id FROM users u WHERE u.user_id IS DISTINCT FROM alerts.reviewed_by LIMIT 1)"
CHANGES = {
    "loans": {
        "loan_id": "gen_random_uuid()", "facility_id": "gen_random_uuid()", "vin": "'1HGCM82633A004352'",
        "first_submission_id": "gen_random_uuid()", "created_at": "created_at - interval '1 day'",
        "borrower_ref": "'ZZZZ9999'", "origination_date": "origination_date - 1",
        "original_amount_cents": "original_amount_cents + 1", "lender_loan_number": "'CHANGED-1'",
        "current_balance_cents": "current_balance_cents - 1",
        "updated_at": "updated_at + interval '1 second'",
    },
    "tape_submissions": {
        "submission_id": "gen_random_uuid()", "facility_id": "gen_random_uuid()", "kind": "'BACK_BOOK'",
        "source": "'API_INSTRUCTION'", "file_name": "'other.csv'", "file_sha256": "repeat('0', 64)",
        "rows_sha256": "repeat('0', 64)", "has_bom": "NOT has_bom", "header_text": "header_text || 'x'",
        "header_cells": "'[\"x\"]'::jsonb", "trailer_text": "trailer_text || 'x'",
        "as_of_date": "as_of_date - 1", "row_count": "row_count + 1", "uploaded_by": "gen_random_uuid()",
        "uploaded_at": "uploaded_at - interval '1 day'", "attempts": "attempts + 1",
        "next_attempt_at": "now() + interval '1 hour'", "updated_at": "updated_at + interval '1 second'",
    },
    "alerts": {
        "alert_id": "gen_random_uuid()", "registry_case_id": "gen_random_uuid()",
        "loan_id": "(SELECT loan_id FROM loans ORDER BY loan_id DESC LIMIT 1)", "role": "'HOLDER'",
        "match_kind": "'LIKELY'", "match_basis": "'FIELDS'", "kinds_matched": "ARRAY['VIN']",
        "kinds_not_matched": "ARRAY['ASSET']", "received_at": "received_at - interval '1 day'",
        "explanation_text": "'rewritten'", "explanation_source": "'MODEL'", "note": "'a note'",
        "reviewed_by": OTHER_USER, "reviewed_at": "coalesce(reviewed_at + interval '1 second', now())",
        "bank_position": "CASE WHEN bank_position IS NULL THEN 'DATA_ERROR' "
                         "ELSE 'CONFIRMED_DOUBLE_PLEDGE' END",
        "position_submitted_at": "coalesce(position_submitted_at + interval '1 hour', now())",
    },
}  # fmt: skip
KEYS = {"loans": "loan_id", "tape_submissions": "submission_id", "alerts": "alert_id"}


@pytest.fixture(scope="module")
def bank(example_data: Path, example_outside: InProcessOutsideRecords) -> Iterator[BankDatabase]:
    database = new_bank_database()
    provision_and_migrate(database)
    seed_reference(database, example_data)
    doubles = InProcessDoubles()
    with BankAgentHarness(database, Services(example_outside, doubles)) as agent:
        tape = agent.ingest(
            EXAMPLE_TAPES / "example_tape_20.csv", "HARBOR-SUMMIT", TapeKind.PLEDGE, date(2026, 10, 15)
        )
        agent.run_until(tape, TapeStatus.CHECKED)
    doubles.close()
    yield database
    drop(database)


def update(table: str, key: uuid.UUID, change: str) -> Action:
    """`UPDATE table SET change WHERE key = %s`, the change written in this file."""
    query = sql.SQL("UPDATE {} SET {} WHERE {} = %s").format(
        sql.Identifier(table), sql.SQL(change), sql.Identifier(KEYS[table])
    )
    return statement(query, (key,))


@dataclass(frozen=True)
class State:
    """A row reached by legal steps only, its key, and the steps that reach it."""

    name: str
    key: uuid.UUID
    steps: tuple[Action, ...]


def loan_states(bank: BankDatabase) -> list[State]:
    row = new_loan(bank, 70)
    key = row["loan_id"]
    assert isinstance(key, uuid.UUID)
    pending = (values_insert("loans", [row]),)
    token = statement(
        sql.SQL("INSERT INTO loan_tokens VALUES (%s, 'ASSET', '1.1', repeat('c', 64), now())"), (key,)
    )
    tokenised = (*pending, token)
    locked = (*tokenised, update("loans", key, "state = 'LOCKED'"))
    released = (*locked, update("loans", key, "state = 'RELEASED'"))
    return [State("PENDING", key, pending), State("TOKENISED", key, tokenised),
            State("LOCKED", key, locked), State("RELEASED", key, released)]  # fmt: skip


def tape_states(bank: BankDatabase) -> list[State]:
    row = new_tape(bank)
    key = row["submission_id"]
    assert isinstance(key, uuid.UUID)
    received = (values_insert("tape_submissions", [row]),)
    failed = (*received, update("tape_submissions", key, "status = 'FAILED', resume_status = 'RECEIVED', "
                                "failed_stage = 'CLEANED', failure_code = 'INTERNAL_ERROR'"))  # fmt: skip
    forward = [
        update("tape_submissions", key, f"status = '{s}'")
        for s in ("CLEANED", "CHECKED", "TOKENIZED", "SUBMITTED")
    ]
    completed = (
        *received,
        *forward,
        update("tape_submissions", key, "status = 'COMPLETED', completed_at = now()"),
    )
    return [
        State("RECEIVED", key, received),
        State("FAILED", key, failed),
        State("COMPLETED", key, completed),
    ]


def alert_states(bank: BankDatabase) -> list[State]:
    row = new_alert(bank)
    key = row["alert_id"]
    assert isinstance(key, uuid.UUID)
    new = (values_insert("alerts", [row]),)
    reviewing = (
        *new,
        update("alerts", key, f"status = 'REVIEWING', reviewed_by = {USER}, reviewed_at = now()"),
    )
    positioned = (
        *reviewing,
        update("alerts", key, "bank_position = 'DATA_ERROR', position_submitted_at = now()"),
    )
    cleared = (*new, update("alerts", key, "status = 'CLEARED', case_status = 'CLEARED_DATA_ERROR'"))
    escalated = (*positioned, update("alerts", key, "status = 'ESCALATED', case_status = 'CONFIRMED'"))
    return [State("NEW", key, new), State("REVIEWING", key, reviewing), State("POSITIONED", key, positioned),
            State("CLEARED", key, cleared), State("ESCALATED", key, escalated)]  # fmt: skip


STATES = {"loans": loan_states, "tape_submissions": tape_states, "alerts": alert_states}
FROZEN = {"loans": {"RELEASED"}, "tape_submissions": {"COMPLETED"}, "alerts": {"CLEARED", "ESCALATED"}}
CONDITIONAL_ALLOWED = {
    ("loans", "PENDING"): set(GUARDED_COLUMNS["loans"]),
    ("tape_submissions", "RECEIVED"): {"attempts", "next_attempt_at"},
    ("alerts", "NEW"): {"note", "reviewed_by", "reviewed_at"},
    ("alerts", "REVIEWING"): {"note", "reviewed_by", "reviewed_at", "bank_position", "position_submitted_at"},
    ("alerts", "POSITIONED"): {"note", "reviewed_by", "reviewed_at"},
}


def expected(table: str, state: str, column: str) -> str | None:
    """The SQLSTATE a change of `column` alone gets in `state`, by the classes of decision 87."""
    if state in FROZEN[table]:
        return FROZEN_CODES[table]
    kind = GUARDED_COLUMNS[table][column]
    if kind is ColumnClass.IMMUTABLE:
        return GUARD_CODES[table]
    if kind is ColumnClass.CONDITIONAL and column not in CONDITIONAL_ALLOWED.get((table, state), set()):
        return GUARD_CODES[table]
    return None


@pytest.mark.parametrize("table", sorted(GUARDED_COLUMNS))
def test_every_column_of_a_guarded_table_is_classified(bank: BankDatabase, table: str) -> None:
    with bank.connect("admin") as connection:
        columns = {
            str(row[0])
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = %s",
                (table,),
            )
        }
    assert columns == set(GUARDED_COLUMNS[table])


CASES = [
    (table, column)
    for table, columns in sorted(GUARDED_COLUMNS.items())
    for column in sorted(columns)
    if column in CHANGES[table]
]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(("table", "column"), CASES, ids=[f"{t}.{c}" for t, c in CASES])
def test_each_column_changed_alone_gets_its_guards_answer_in_every_state(
    bank: BankDatabase, role: str, table: str, column: str
) -> None:
    outcomes = {}
    wanted = {}
    for state in STATES[table](bank):
        change = update(table, state.key, f"{column} = {CHANGES[table][column]}")
        assert attempt(bank, role, state.steps) is None, f"{state.name} is reached by legal steps"
        outcomes[state.name] = attempt(bank, role, [*state.steps, change])
        wanted[state.name] = expected(table, state.name, column)
    assert outcomes == wanted


def test_every_immutable_and_conditional_column_has_a_change_case() -> None:
    guarded = {
        (table, column)
        for table, columns in GUARDED_COLUMNS.items()
        for column, kind in columns.items()
        if kind is not ColumnClass.MUTABLE
    }
    assert guarded <= set(CASES)


F3 = [
    "status = 'CLEARED'",
    "status = 'CLEARED', case_status = 'CONFIRMED'",
    "status = 'ESCALATED', case_status = 'CLEARED_DATA_ERROR'",
]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("change", F3)
def test_an_alert_never_closes_without_its_case(bank: BankDatabase, role: str, change: str) -> None:
    (new, reviewing, *_) = alert_states(bank)
    for state in (new, reviewing):
        assert attempt(bank, role, [*state.steps, update("alerts", state.key, change)]) == "OP006", state.name
    switched_off = statement(sql.SQL("ALTER TABLE alerts DISABLE TRIGGER alerts_guard"))
    steps = [*new.steps, switched_off, update("alerts", new.key, change)]
    assert attempt(bank, "owner", steps) == "23514", "the check constraint holds without the guard"
