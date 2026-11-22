"""The insert and update guards hold under every statement form (SPEC_DECISIONS 68, item 4).

A row trigger runs for each row whatever statement wrote it. These tests show it for the
three guarded tables (`tape_submissions`, `loans`, `alerts`) and the forms a hand or a tool
might use instead of the repositories: a multi-row `VALUES` insert, `INSERT ... SELECT`,
`COPY`, and `INSERT ... ON CONFLICT DO UPDATE`. Each attempt runs in one transaction that is
rolled back, as the app role and as the owner. A legal row passes every form, so a refusal
is the guard and not the form.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from bank_testkit.data import EXAMPLE_TAPES, seed_reference
from bank_testkit.database import BankDatabase, drop, new_bank_database, provision_and_migrate
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.memory.bank import vins
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from onepledge_bank_agent.domain.enums import TapeKind, TapeStatus

pytestmark = pytest.mark.integration

ROLES = ("app", "owner")
JSONB_OID = 3802
type Row = dict[str, object]
type Action = Callable[[psycopg.Connection[tuple[object, ...]]], object]


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


def attempt(database: BankDatabase, role: str, actions: Sequence[Action]) -> str | None:
    """Run the actions in one rolled back transaction; the SQLSTATE of the first that fails, or None."""
    with database.connect(role) as connection:
        connection.autocommit = False
        try:
            for action in actions:
                action(connection)
        except psycopg.Error as error:
            return error.sqlstate
        finally:
            connection.rollback()
    return None


def stored_row(database: BankDatabase, table: str, order: str) -> Row:
    """One stored row of a table, its jsonb values wrapped so that they can be written back."""
    with database.connect("owner") as connection:
        cursor = connection.execute(sql.SQL("SELECT * FROM {} ORDER BY {} LIMIT 1").format(
            sql.Identifier(table), sql.Identifier(order)))  # fmt: skip
        values = cursor.fetchone()
        assert values is not None and cursor.description is not None
        return {
            column.name: Jsonb(value) if column.type_code == JSONB_OID else value
            for column, value in zip(cursor.description, values, strict=True)
        }


def new_tape(database: BankDatabase, **changes: object) -> Row:
    """A fresh RECEIVED tape row copied from the stored one, with changes."""
    fresh = {
        "submission_id": uuid.uuid4(), "file_sha256": uuid.uuid4().hex * 2, "status": "RECEIVED",
        "attempts": 0, "resume_status": None, "failed_stage": None, "failure_code": None,
        "completed_at": None, "next_attempt_at": None,
    }  # fmt: skip
    return {**stored_row(database, "tape_submissions", "uploaded_at"), **fresh, **changes}


def new_loan(database: BankDatabase, serial: int, **changes: object) -> Row:
    """A fresh PENDING loan row copied from a stored one, with a VIN of its own and changes."""
    fresh = {"loan_id": uuid.uuid4(), "vin": vins(1, 900 + serial)[0], "state": "PENDING"}
    return {**stored_row(database, "loans", "loan_id"), **fresh, **changes}


def new_alert(database: BankDatabase, **changes: object) -> Row:
    """A NEW alert on an OPEN case for a stored loan, with changes."""
    loan = stored_row(database, "loans", "loan_id")
    row: Row = {
        "alert_id": uuid.uuid4(), "registry_case_id": uuid.uuid4(), "loan_id": loan["loan_id"],
        "role": "CLAIMANT", "match_kind": "EXACT", "match_basis": "ASSET", "kinds_matched": ["ASSET"],
        "kinds_not_matched": ["VIN"], "explanation_text": "An explanation.", "explanation_source": "TEMPLATE",
        "status": "NEW", "case_status": "OPEN", "received_at": loan["created_at"],
    }  # fmt: skip
    return {**row, **changes}


def values_insert(table: str, rows: Sequence[Mapping[str, object]], conflict: str = "") -> Action:
    """One `INSERT ... VALUES (...), (...)` of every row, optionally followed by an ON CONFLICT clause."""
    columns = list(rows[0])
    row_sql = sql.SQL("({})").format(sql.SQL(", ").join(sql.Placeholder() * len(columns)))
    statement = sql.SQL("INSERT INTO {} ({}) VALUES {} {}").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join([row_sql] * len(rows)), sql.SQL(conflict),
    )  # fmt: skip
    parameters = [row[column] for row in rows for column in columns]
    return lambda connection: connection.execute(statement, parameters)


def select_insert(table: str, rows: Sequence[Mapping[str, object]]) -> Action:
    """`INSERT ... SELECT` of rows produced by a query (`jsonb_populate_recordset`), not a VALUES list."""
    records = [{k: v.obj if isinstance(v, Jsonb) else v for k, v in row.items()} for row in rows]
    statement = sql.SQL("INSERT INTO {} SELECT * FROM jsonb_populate_recordset(NULL::{}, %s)").format(
        sql.Identifier(table), sql.Identifier(table)
    )
    document = Jsonb(json.loads(json.dumps(records, default=str)))
    return lambda connection: connection.execute(statement, (document,))


def copy_in(table: str, rows: Sequence[Mapping[str, object]]) -> Action:
    """`COPY ... FROM STDIN` of every row."""
    columns = list(rows[0])

    def run(connection: psycopg.Connection[tuple[object, ...]]) -> None:
        statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
            sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
        )
        with connection.cursor().copy(statement) as copy:
            for row in rows:
                copy.write_row([row[column] for column in columns])

    return run


FORMS: dict[str, Callable[[str, Sequence[Mapping[str, object]]], Action]] = {
    "values": values_insert, "select": select_insert, "copy": copy_in,
}  # fmt: skip


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("form", list(FORMS))
def test_every_insert_form_passes_the_insert_guards(bank: BankDatabase, role: str, form: str) -> None:
    write = FORMS[form]
    tape, loan, alert = "tape_submissions", "loans", "alerts"
    cases: list[tuple[str, list[Row], list[Row], str]] = [
        (tape, [new_tape(bank), new_tape(bank)], [new_tape(bank), new_tape(bank, status="CLEANED")], "OP004"),
        (tape, [], [new_tape(bank, attempts=3)], "OP004"),
        (tape, [], [new_tape(bank, failure_code="INTERNAL_ERROR")], "OP004"),
        (loan, [new_loan(bank, 1), new_loan(bank, 2)], [new_loan(bank, 3), new_loan(bank, 4, state="LOCKED")],
         "OP002"),
        (alert, [new_alert(bank), new_alert(bank)], [new_alert(bank), new_alert(bank, status="CLEARED")],
         "OP006"),
        (alert, [], [new_alert(bank, bank_position="DATA_ERROR")], "OP006"),
        (alert, [], [new_alert(bank, case_status="CONFIRMED")], "OP006"),
    ]  # fmt: skip
    for table, legal, mixed, code in cases:
        if legal:
            assert attempt(bank, role, [write(table, legal)]) is None, (table, "legal rows")
        assert attempt(bank, role, [write(table, mixed)]) == code, (table, mixed[-1])


def upsert(table: str, key: str, row: Mapping[str, object], change: str) -> Action:
    """`INSERT ... ON CONFLICT (key) DO UPDATE SET change` of a row whose key is already stored."""
    return values_insert(table, [row], f"ON CONFLICT ({key}) DO UPDATE SET {change}")


@pytest.mark.parametrize("role", ROLES)
def test_an_upsert_passes_the_update_guards(bank: BankDatabase, role: str) -> None:
    tape = new_tape(bank)
    stored_tape = [values_insert("tape_submissions", [tape])]
    assert attempt(bank, role, [*stored_tape, upsert("tape_submissions", "submission_id", tape,
                                                     "status = 'CLEANED'")]) is None  # fmt: skip
    for illegal in (
        "status = 'COMPLETED'",
        "file_sha256 = repeat('0', 64)",
        "failure_code = 'INTERNAL_ERROR'",
    ):
        action = upsert("tape_submissions", "submission_id", tape, illegal)
        assert attempt(bank, role, [*stored_tape, action]) == "OP004", illegal
    loan = new_loan(bank, 5)
    stored_loan = [values_insert("loans", [loan])]
    assert (
        attempt(bank, role, [*stored_loan, upsert("loans", "loan_id", loan, "state = 'HELD_LOCAL'")]) is None
    )
    for illegal, code in (
        ("state = 'RELEASED'", "OP002"),
        ("first_submission_id = gen_random_uuid()", "OP003"),
    ):
        assert attempt(bank, role, [*stored_loan, upsert("loans", "loan_id", loan, illegal)]) == code, illegal
    alert = new_alert(bank)
    stored_alert = [values_insert("alerts", [alert])]
    closed = upsert("alerts", "alert_id", alert, "status = 'CLEARED', case_status = 'CLEARED_DATA_ERROR'")
    assert attempt(bank, role, [*stored_alert, closed]) is None
    for illegal in ("note = 'changed'", "case_status = 'CONFIRMED'", "status = 'ESCALATED'"):
        action = upsert("alerts", "alert_id", alert, illegal)
        assert attempt(bank, role, [*stored_alert, closed, action]) == "OP006", illegal
    reopened = upsert("alerts", "alert_id", alert, "status = 'NEW'")
    assert attempt(bank, role, [*stored_alert, upsert("alerts", "alert_id", alert, "status = 'REVIEWING'"),
                                reopened]) == "OP006"  # fmt: skip


def alert_update(alert: uuid.UUID, change: str) -> Action:
    """One plain update of an alert."""
    statement = sql.SQL("UPDATE alerts SET {} WHERE alert_id = %s").format(sql.SQL(change))
    return lambda connection: connection.execute(statement, (alert,))


@pytest.mark.parametrize("role", ROLES)
def test_a_case_leaves_open_once_and_a_closed_alert_never_changes_in_any_column(
    bank: BankDatabase, role: str
) -> None:
    row = new_alert(bank)
    alert = row["alert_id"]
    assert isinstance(alert, uuid.UUID)
    stored = [values_insert("alerts", [row])]
    user = "(SELECT user_id FROM users LIMIT 1)"
    review = alert_update(alert, f"status = 'REVIEWING', reviewed_by = {user}, reviewed_at = now()")
    left = alert_update(alert, "case_status = 'CLEARED_LATE_RELEASE'")
    for second in ("CLEARED_DATA_ERROR", "CONFIRMED"):
        assert attempt(
            bank, role, [*stored, review, left, alert_update(alert, f"case_status = '{second}'")]
        ) == ("OP006"), second
    position = alert_update(alert, "bank_position = 'DATA_ERROR', position_submitted_at = now()")
    for final in (
        "status = 'CLEARED', case_status = 'CLEARED_DATA_ERROR'",
        "status = 'ESCALATED', case_status = 'CONFIRMED'",
    ):
        closed = [*stored, review, position, alert_update(alert, final)]
        assert attempt(bank, role, closed) is None, final
        for column_change in (
            "explanation_text = 'rewritten'", "explanation_source = 'MODEL'", "note = 'late note'",
            "reviewed_by = NULL", "reviewed_at = now() + interval '1 hour'",
            "position_submitted_at = now() + interval '1 hour'", "bank_position = 'CONFIRMED_DOUBLE_PLEDGE'",
            "bank_position = NULL", "status = 'REVIEWING'", "case_status = 'OPEN'",
        ):  # fmt: skip
            assert attempt(bank, role, [*closed, alert_update(alert, column_change)]) == "OP006", (
                column_change
            )
        assert attempt(bank, role, [*closed, alert_update(alert, "status = status")]) is None, (
            "a no-op update"
        )
