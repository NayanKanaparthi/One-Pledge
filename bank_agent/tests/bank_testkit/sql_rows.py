"""Rows of the guarded tables built in SQL, and statements run in one rolled back transaction.

For the guard tests (SPEC_DECISIONS 68.4, 86, 87): copy a stored row with changes, insert
it by a VALUES list, run steps as the app role or the owner, and read the SQLSTATE of the
first refusal. Every attempt is rolled back, so one filled database serves every case.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from bank_testkit.database import BankDatabase
from bank_testkit.memory.bank import vins

JSONB_OID = 3802
type Row = dict[str, object]
type Action = Callable[[psycopg.Connection[tuple[object, ...]]], object]


def statement(query: sql.SQL | sql.Composed, parameters: Sequence[object] = ()) -> Action:
    """One statement with its parameters."""
    return lambda connection: connection.execute(query, parameters)


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


def new_request(database: BankDatabase, **changes: object) -> Row:
    """An unanswered RESUBMIT registry request of a stored tape, with changes."""
    tape = stored_row(database, "tape_submissions", "uploaded_at")
    row: Row = {
        "request_id": uuid.uuid4(), "submission_id": tape["submission_id"], "origin": "RESUBMIT",
        "batch_index": 0, "idempotency_key": uuid.uuid4().hex * 2, "kind": "PLEDGE", "key_version": "1.1",
        "item_count": 1, "attempts": 0,
    }  # fmt: skip
    return {**row, **changes}


def new_item(database: BankDatabase, request_id: object, **changes: object) -> Row:
    """An item without a result of that request, for a stored loan, with changes."""
    loan = stored_row(database, "loans", "loan_id")
    row: Row = {"request_id": request_id, "item_ref": uuid.uuid4(), "position": 0, "loan_id": loan["loan_id"]}
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
