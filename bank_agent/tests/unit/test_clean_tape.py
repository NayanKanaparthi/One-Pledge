"""`CleanTape` (SPEC 10.4, 10.5, 13.2) over in-memory ports."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import pytest

from bank_testkit.tapes import summit_row, summit_tape
from bank_testkit.world import CrashError, World
from onepledge_bank_agent.application.clean_tape import CleanTape
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.ingest_tape import IngestCommand, IngestTape
from onepledge_bank_agent.application.ports import StoredRawRow
from onepledge_bank_agent.domain.access_policy import AccessPolicy
from onepledge_bank_agent.domain.audit import Actor
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    CleaningProblem,
    LoanState,
    Role,
    RowDisposition,
    TapeKind,
    TapeSource,
    TapeStatus,
)
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.tape import TapeFacts, TapeSubmission
from onepledge_bank_agent.domain.tape_file import TapeFileReader, TapeLayout, row_sha256

pytestmark = pytest.mark.unit

OFFICER = Actor.user(uuid.uuid4(), Role.CREDIT_OFFICER)
SECOND = "5YJ3E1EA2KF317000"


@pytest.fixture
def world() -> World:
    return World()


def upload(
    world: World, rows: list[list[str]], kind: TapeKind = TapeKind.PLEDGE, name: str = "t.csv"
) -> uuid.UUID:
    data = summit_tape(rows)
    command = IngestCommand(world.facility.facility_id, kind, date(2026, 10, 15), name, data, OFFICER)
    return (
        IngestTape(world.units, TapeFileReader(), AccessPolicy(), world.runtime)
        .execute(command)
        .submission_id
    )


def clean(world: World, submission_id: uuid.UUID) -> None:
    CleanTape(world.units, world.runtime).run(submission_id)


def dispositions(world: World, submission_id: uuid.UUID) -> list[RowDisposition]:
    return [
        item.disposition
        for item in sorted(world.store.state.items[submission_id], key=lambda i: i.row_number)
    ]


def loan_by_vin(world: World, vin: str) -> Loan:
    return next(
        loan
        for loan in world.store.state.loans.values()
        if loan.terms.vin.value == vin and loan.state is not LoanState.RELEASED
    )


def force_state(world: World, vin: str, state: LoanState) -> None:
    loan = loan_by_vin(world, vin)
    world.store.state.loans[loan.loan_id] = Loan(replace(loan.record, state=state))


def test_a_new_tape_creates_pending_loans_items_issues_and_audits(world: World) -> None:
    rows = [
        summit_row(),
        summit_row(vin="1hgcm82633a004352"),
        summit_row(vin=SECOND.lower()),
        ["TOTAL", "", "", "", "", "$1.00"],
    ]
    submission = upload(world, rows)
    clean(world, submission)
    state = world.store.state
    assert state.tapes[submission].status is TapeStatus.CLEANED
    assert dispositions(world, submission) == [
        RowDisposition.ACCEPTED_NEW,
        RowDisposition.REJECTED,
        RowDisposition.ACCEPTED_NEW,
        RowDisposition.REJECTED,
    ]
    items = sorted(state.items[submission], key=lambda i: i.row_number)
    assert [i.problem for i in items[1].issues] == [
        CleaningProblem.VIN_CASE_NORMALISED,
        CleaningProblem.DUPLICATE_VIN_IN_FILE,
    ]
    assert {loan.state for loan in state.loans.values()} == {LoanState.PENDING} and len(state.loans) == 2
    assert all(loan.record.first_submission_id == submission for loan in state.loans.values())
    changes = world.audit(AuditAction.LOAN_STATE_CHANGED)
    assert {(c.detail["from"], c.detail["to"], c.detail["reason_code"]) for c in changes} == {
        (None, "PENDING", "ACCEPTED_NEW")
    }
    assert len(changes) == 2
    (advanced,) = world.audit(AuditAction.TAPE_STAGE_ADVANCED)
    assert dict(advanced.detail) == {
        "from": "RECEIVED", "to": "CLEANED", "rows": 4, "rows_rejected": 2, "rows_fixed": 1,
        "accepted_new": 2, "accepted_represented": 0, "already_locked": 0, "release_requested": 0,
    }  # fmt: skip


def test_running_the_stage_again_changes_nothing(world: World) -> None:
    submission = upload(world, [summit_row()])
    clean(world, submission)
    commits = world.store.commits
    clean(world, submission)
    clean(world, uuid.uuid4())
    assert world.store.commits == commits


def test_a_crash_before_the_commit_leaves_the_tape_received_with_no_derived_rows(world: World) -> None:
    submission = upload(world, [summit_row()])
    world.faults.arm(FaultPoint.CLEAN_BEFORE_COMMIT)
    with pytest.raises(CrashError):
        clean(world, submission)
    state = world.store.state
    assert (
        state.tapes[submission].status is TapeStatus.RECEIVED
        and not state.loans
        and submission not in state.items
    )
    clean(world, submission)
    assert state is not world.store.state and world.store.state.tapes[submission].status is TapeStatus.CLEANED


def test_a_held_loan_presented_again_goes_back_to_pending_with_new_terms(world: World) -> None:
    first = upload(world, [summit_row()], name="a.csv")
    clean(world, first)
    force_state(world, "1HGCM82633A004352", LoanState.HELD_LOCAL)
    second = upload(world, [summit_row(original="$30,000.00", loan_number="SAF-9")], name="b.csv")
    clean(world, second)
    loan = loan_by_vin(world, "1HGCM82633A004352")
    assert dispositions(world, second) == [RowDisposition.ACCEPTED_REPRESENTED]
    assert loan.state is LoanState.PENDING and loan.terms.original_amount.cents == 3_000_000
    assert loan.record.lender_loan_number.value == "SAF-9" and loan.record.first_submission_id == first
    represented = [c for c in world.audit(AuditAction.LOAN_STATE_CHANGED) if c.detail["from"] == "HELD_LOCAL"]
    assert represented[0].detail["reason_code"] == "ACCEPTED_REPRESENTED"


def test_a_tokenised_loan_keeps_its_terms(world: World) -> None:
    first = upload(world, [summit_row()], name="a.csv")
    clean(world, first)
    world.store.state.tokens.add(loan_by_vin(world, "1HGCM82633A004352").loan_id)
    changed = upload(world, [summit_row(original="$30,000.00")], name="b.csv")
    clean(world, changed)
    assert (
        world.store.state.items[changed][0].issues[-1].problem
        is CleaningProblem.TERMS_CHANGED_AFTER_TOKENIZATION
    )
    same = upload(world, [summit_row(balance="$17,000.00")], name="c.csv")
    clean(world, same)
    loan = loan_by_vin(world, "1HGCM82633A004352")
    assert dispositions(world, same) == [RowDisposition.ACCEPTED_REPRESENTED]
    assert loan.record.current_balance.cents == 1_700_000 and loan.terms.original_amount.cents == 2_345_017


def test_a_locked_loan_only_takes_a_new_balance_and_a_conflict_is_refused(world: World) -> None:
    first = upload(world, [summit_row(), summit_row(vin=SECOND)], name="a.csv")
    clean(world, first)
    force_state(world, "1HGCM82633A004352", LoanState.LOCKED)
    force_state(world, SECOND, LoanState.CONFLICT)
    second = upload(world, [summit_row(balance="$10,000.00"), summit_row(vin=SECOND)], name="b.csv")
    clean(world, second)
    assert dispositions(world, second) == [RowDisposition.ALREADY_LOCKED, RowDisposition.REJECTED]
    assert world.store.state.items[second][1].issues[-1].problem is CleaningProblem.OPEN_CONFLICT
    locked = loan_by_vin(world, "1HGCM82633A004352")
    assert locked.state is LoanState.LOCKED and locked.record.current_balance.cents == 1_000_000
    assert len(world.audit(AuditAction.LOAN_STATE_CHANGED)) == 2


def test_a_release_file_requests_the_locked_loans_only(world: World) -> None:
    first = upload(world, [summit_row(), summit_row(vin=SECOND)], name="a.csv")
    clean(world, first)
    force_state(world, "1HGCM82633A004352", LoanState.LOCKED)
    release = upload(world, [summit_row(), summit_row(vin=SECOND)], TapeKind.RELEASE, name="r.csv")
    clean(world, release)
    assert dispositions(world, release) == [RowDisposition.RELEASE_REQUESTED, RowDisposition.REJECTED]
    assert world.store.state.items[release][1].issues[-1].problem is CleaningProblem.RELEASE_LOAN_NOT_LOCKED
    assert loan_by_vin(world, "1HGCM82633A004352").state is LoanState.LOCKED


def test_a_release_instruction_is_resolved_by_loan_id_within_the_facility(world: World) -> None:
    first = upload(world, [summit_row(), summit_row(vin=SECOND)], name="a.csv")
    clean(world, first)
    force_state(world, "1HGCM82633A004352", LoanState.LOCKED)
    force_state(world, SECOND, LoanState.LOCKED)
    locked, foreign = loan_by_vin(world, "1HGCM82633A004352"), loan_by_vin(world, SECOND)
    world.store.state.loans[foreign.loan_id] = Loan(
        replace(foreign.record, facility_id=world.other_facility.facility_id)
    )
    submission = uuid.uuid4()
    loan_ids = [str(locked.loan_id), str(foreign.loan_id), "not-a-uuid"]
    facts = TapeFacts(
        submission, world.facility.facility_id, TapeKind.RELEASE, TapeSource.API_INSTRUCTION,
        "release-instruction.json", "a" * 64, "b" * 64, date(2026, 11, 12), 3, uuid.uuid4(),
        world.clock.now(),
    )  # fmt: skip
    world.store.state.tapes[submission] = TapeSubmission.receive(facts)
    world.store.state.layouts[submission] = TapeLayout(False, "loan_id\n", ("loan_id",), "")
    world.store.state.raw_rows[submission] = [
        StoredRawRow(uuid.uuid4(), number, f"{loan_id}\n", (loan_id,), row_sha256(f"{loan_id}\n"))
        for number, loan_id in enumerate(loan_ids, start=1)
    ]
    clean(world, submission)
    assert dispositions(world, submission) == [
        RowDisposition.RELEASE_REQUESTED,
        RowDisposition.REJECTED,
        RowDisposition.REJECTED,
    ]
    assert world.store.state.items[submission][0].loan_id == locked.loan_id
