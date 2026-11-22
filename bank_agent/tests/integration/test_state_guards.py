"""The state machines of SPEC 8.3, 8.4 and 8.5 are the only path, in the database too
(SPEC_DECISIONS 46 and 47; WP2 review 1, F1 and quality finding 1).

Every attempt runs in one transaction that is rolled back, as the app role and as the
owner. A tape in each status is reached from a fresh RECEIVED tape by legal updates only.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from bank_testkit.data import EXAMPLE_TAPES, seed_reference
from bank_testkit.database import BankDatabase, drop, new_bank_database, provision_and_migrate
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from onepledge_bank_agent.domain.enums import TapeKind, TapeStatus

pytestmark = pytest.mark.integration

ROLES = ("app", "owner")
FORWARD = ["RECEIVED", "CLEANED", "CHECKED", "TOKENIZED", "SUBMITTED", "COMPLETED"]
NEXT = dict(itertools.pairwise(FORWARD))
NON_TERMINAL = FORWARD[:-1]
STATUSES = [*FORWARD, "FAILED"]
type Step = tuple[str | sql.Composed, tuple[object, ...]]

NEW_TAPE = (
    "INSERT INTO tape_submissions SELECT (jsonb_populate_record(NULL::tape_submissions, to_jsonb(t) || "
    "jsonb_build_object('submission_id', %s::uuid, 'file_sha256', encode(sha256(%s::bytea), 'hex'), "
    "'status', 'RECEIVED', 'attempts', 0, 'resume_status', NULL, 'failed_stage', NULL, "
    "'failure_code', NULL, 'completed_at', NULL, 'next_attempt_at', NULL))).* "
    "FROM tape_submissions t LIMIT 1"
)


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


def attempt(database: BankDatabase, role: str, steps: Sequence[Step]) -> str | None:
    """Run the steps in one transaction; the SQLSTATE of the first that fails, or None. Always rolled back."""
    with database.connect(role) as connection:
        connection.autocommit = False
        try:
            for statement, parameters in steps:
                connection.execute(statement, parameters)
        except psycopg.Error as error:
            return error.sqlstate
        finally:
            connection.rollback()
    return None


def update(tape: uuid.UUID, change: str) -> Step:
    return (
        sql.SQL("UPDATE tape_submissions SET {} WHERE submission_id = %s").format(sql.SQL(change)),
        (tape,),
    )


def to_status(tape: uuid.UUID, status: str) -> list[Step]:
    """The legal path from RECEIVED to `status`; FAILED is reached from CLEANED (resume CLEANED)."""
    steps: list[Step] = [(NEW_TAPE, (tape, str(tape)))]
    target = "CLEANED" if status == "FAILED" else status
    for current in FORWARD[1 : FORWARD.index(target) + 1]:
        completed = ", completed_at = now()" if current == "COMPLETED" else ""
        steps.append(update(tape, f"status = '{current}'{completed}"))
    if status == "FAILED":
        steps.append(fail_from(tape, "CLEANED"))
    return steps


def fail_from(tape: uuid.UUID, status: str, resume: str | None = None, stage: str | None = None) -> Step:
    resume = resume or status
    stage = stage or NEXT.get(status, "COMPLETED")
    return update(
        tape,
        f"status = 'FAILED', resume_status = '{resume}', failed_stage = '{stage}', "
        "failure_code = 'INTERNAL_ERROR'",
    )


def change_to(tape: uuid.UUID, origin: str, target: str) -> Step:
    """The honest form of a change from `origin` to `target`: failure columns as a legal change sets them."""
    if target == "FAILED":
        return fail_from(tape, "CLEANED" if origin == "FAILED" else origin)
    completed = ", completed_at = now()" if target == "COMPLETED" else ""
    return update(
        tape,
        f"status = '{target}', resume_status = NULL, failed_stage = NULL, failure_code = NULL{completed}",
    )


def legal(origin: str, target: str) -> bool:
    if origin == target:
        return True
    if origin == "FAILED":
        return target == "CLEANED"
    return NEXT.get(origin) == target or (target == "FAILED" and origin in NON_TERMINAL)


@pytest.mark.parametrize("role", ROLES)
def test_every_pair_of_tape_statuses_is_allowed_only_when_spec_8_3_allows_it(
    bank: BankDatabase, role: str
) -> None:
    refused = []
    for origin in STATUSES:
        for target in STATUSES:
            tape = uuid.uuid4()
            outcome = attempt(bank, role, [*to_status(tape, origin), change_to(tape, origin, target)])
            if outcome != (None if legal(origin, target) else "OP004"):
                refused.append((origin, target, outcome))
    assert refused == []


@pytest.mark.parametrize("role", ROLES)
def test_the_forged_resumes_of_the_review_are_refused(bank: BankDatabase, role: str) -> None:
    tape = uuid.uuid4()
    received = [(NEW_TAPE, (tape, str(tape)))]
    forged_fail = fail_from(tape, "RECEIVED", resume="SUBMITTED", stage="COMPLETED")
    assert attempt(bank, role, [*received, forged_fail]) == "OP004"
    skip_check = fail_from(tape, "CLEANED", resume="CHECKED", stage="TOKENIZED")
    assert attempt(bank, role, [*to_status(tape, "CLEANED"), skip_check]) == "OP004"
    honest = to_status(tape, "FAILED")
    for rewrite in (
        "resume_status = 'SUBMITTED'",
        "failed_stage = 'COMPLETED'",
        "failure_code = 'REGISTRY_UNAVAILABLE'",
        "resume_status = NULL",
    ):
        assert attempt(bank, role, [*honest, update(tape, rewrite)]) == "OP004", rewrite
    assert attempt(bank, role, [*honest, update(tape, "attempts = attempts + 1")]) == "OP004", (
        "decision 87: the attempts of a FAILED tape are kept"
    )
    for wrong in (
        ("CHECKED", "CHECKED"),
        ("CLEANED", "TOKENIZED"),
        ("CLEANED", "CLEANED"),
        ("RECEIVED", "CLEANED"),
    ):
        resume, stage = wrong
        steps = [*to_status(tape, "CLEANED"), fail_from(tape, "CLEANED", resume=resume, stage=stage)]
        assert attempt(bank, role, steps) == "OP004", wrong
    no_code = update(tape, "status = 'FAILED', resume_status = 'CLEANED', failed_stage = 'CHECKED'")
    assert attempt(bank, role, [*to_status(tape, "CLEANED"), no_code]) == "OP004"
    stray = update(tape, "failure_code = 'INTERNAL_ERROR'")
    assert attempt(bank, role, [*to_status(tape, "CLEANED"), stray]) == "OP004"


@pytest.mark.parametrize("role", ROLES)
def test_a_tape_and_a_loan_are_created_only_in_their_first_state(bank: BankDatabase, role: str) -> None:
    tape = uuid.uuid4()
    assert attempt(bank, role, [(NEW_TAPE, (tape, str(tape)))]) is None
    for column, value in (
        ("status", "'COMPLETED'::text"), ("attempts", "1"), ("failure_code", "'INTERNAL_ERROR'::text"),
        ("resume_status", "'RECEIVED'::text"), ("failed_stage", "'CLEANED'::text"), ("completed_at", "now()"),
        ("next_attempt_at", "now()"),
    ):  # fmt: skip
        statement = NEW_TAPE.replace(
            "'next_attempt_at', NULL", f"'next_attempt_at', NULL, '{column}', to_jsonb({value})"
        )
        assert attempt(bank, role, [(statement, (tape, str(tape)))]) == "OP004", column
    copy = (
        "INSERT INTO loans SELECT gen_random_uuid(), facility_id, first_submission_id, lender_loan_number, "
        "'1HGCM82633A004352', borrower_ref, origination_date, original_amount_cents, current_balance_cents, "
        "%s, now(), now() FROM loans LIMIT 1"
    )
    assert attempt(bank, role, [(copy, ("PENDING",))]) is None
    for state in ("HELD_LOCAL", "LOCKED", "CONFLICT", "RELEASED"):
        assert attempt(bank, role, [(copy, (state,))]) == "OP002", state


ALERT = (
    "INSERT INTO alerts (alert_id, registry_case_id, loan_id, role, match_kind, match_basis, kinds_matched, "
    "kinds_not_matched, explanation_text, explanation_source, status, case_status, received_at) "
    "SELECT %s, gen_random_uuid(), loan_id, 'CLAIMANT', 'EXACT', 'ASSET', ARRAY['ASSET'], ARRAY['VIN'], "
    "'An explanation.', 'TEMPLATE', %s, %s, now() FROM loans ORDER BY loan_id LIMIT 1"
)


def alert_update(alert: uuid.UUID, change: str) -> Step:
    return (sql.SQL("UPDATE alerts SET {} WHERE alert_id = %s").format(sql.SQL(change)), (alert,))


@pytest.mark.parametrize("role", ROLES)
def test_alerts_follow_spec_8_5_on_insert_and_update(bank: BankDatabase, role: str) -> None:
    alert = uuid.uuid4()
    new = [(ALERT, (alert, "NEW", "OPEN"))]
    assert attempt(bank, role, new) is None
    for status, case in (("REVIEWING", "OPEN"), ("CLEARED", "OPEN"), ("NEW", "CONFIRMED")):
        assert attempt(bank, role, [(ALERT, (alert, status, case))]) == "OP006", status
    user = "(SELECT user_id FROM users LIMIT 1)"
    review = alert_update(alert, f"status = 'REVIEWING', reviewed_by = {user}, reviewed_at = now()")
    position = alert_update(alert, "bank_position = 'DATA_ERROR', position_submitted_at = now()")
    cleared = alert_update(alert, "status = 'CLEARED', case_status = 'CLEARED_DATA_ERROR'")
    assert attempt(bank, role, [*new, review, position, cleared]) is None
    assert (
        attempt(bank, role, [*new, alert_update(alert, "status = 'ESCALATED', case_status = 'CONFIRMED'")])
        is None
    )
    for illegal in ("status = 'NEW'", "status = 'REVIEWING'", "note = 'changed'", "case_status = 'OPEN'"):
        assert attempt(bank, role, [*new, cleared, alert_update(alert, illegal)]) == "OP006", illegal
    assert attempt(bank, role, [*new, review, alert_update(alert, "status = 'NEW'")]) == "OP006"
    for frozen in (
        "registry_case_id = gen_random_uuid()", "role = 'HOLDER'", "match_kind = 'LIKELY'",
        "match_basis = 'FIELDS'", "kinds_matched = ARRAY['VIN']", "kinds_not_matched = ARRAY['ASSET']",
        "received_at = now() - interval '1 day'",
        "loan_id = (SELECT loan_id FROM loans ORDER BY loan_id DESC LIMIT 1)",
    ):  # fmt: skip
        assert attempt(bank, role, [*new, alert_update(alert, frozen)]) == "OP006", frozen
    second = alert_update(alert, "bank_position = 'CONFIRMED_DOUBLE_PLEDGE'")
    assert attempt(bank, role, [*new, review, position, second]) == "OP006"
    unset = alert_update(alert, "bank_position = NULL")
    assert attempt(bank, role, [*new, review, position, unset]) == "OP006"
    reopen = alert_update(alert, "case_status = 'OPEN'")
    left = alert_update(alert, "case_status = 'CLEARED_LATE_RELEASE'")
    assert attempt(bank, role, [*new, review, left, reopen]) == "OP006"
