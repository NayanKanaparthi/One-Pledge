"""`RunLocalChecks` (SPEC 11.4, 11.5, 13.2) over in-memory ports and in-memory outside records."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, timedelta

import pytest

from bank_testkit.outside import MemoryOutsideRecords
from bank_testkit.tapes import summit_row, summit_tape
from bank_testkit.world import CrashError, World
from onepledge_bank_agent.application.clean_tape import CleanTape
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.ingest_tape import IngestCommand, IngestTape
from onepledge_bank_agent.application.run_local_checks import OutsideRecordsClients, RunLocalChecks
from onepledge_bank_agent.domain.access_policy import AccessPolicy
from onepledge_bank_agent.domain.audit import Actor
from onepledge_bank_agent.domain.checks import CheckWindows, LocalCheckRules
from onepledge_bank_agent.domain.enums import AuditAction, CheckType, LoanState, Role, TapeKind, TapeStatus
from onepledge_bank_agent.domain.errors import FacilityNotFoundError, OutsideRecordsUnavailableError
from onepledge_bank_agent.domain.outside_records import ContractRecord, PaymentRecord, TitleRecord
from onepledge_bank_agent.domain.tape_file import TapeFileReader

pytestmark = pytest.mark.unit

AS_OF = date(2026, 10, 15)
VINS = ["1HGCM82633A004352", "5YJ3E1EA2KF317000", "WBA3A5C59DF358261", "2T1BURHE7JC034512"]
ORIGINATION = date(2025, 3, 15)


@pytest.fixture
def world() -> World:
    return World()


def outside_for(vins: list[str], held: str | None = None) -> MemoryOutsideRecords:
    """Clean records for every VIN; the `held` VIN has no title (and is old enough to fail)."""
    outside = MemoryOutsideRecords()
    for vin in vins:
        if vin != held:
            outside.titles[vin] = TitleRecord("NJ", "CLEAN", "Summit Auto Finance LLC", None)
        outside.payment_records[vin] = [PaymentRecord(uuid.uuid4(), AS_OF - timedelta(days=5), 100, "HARBOR")]
        outside.contract_records[vin] = [
            ContractRecord(uuid.uuid4(), "SUMMIT AUTO FINANCE LLC", 2345017, ORIGINATION)
        ]
    return outside


def cleaned(world: World, vins: list[str], kind: TapeKind = TapeKind.PLEDGE) -> uuid.UUID:
    data = summit_tape([summit_row(vin=vin) for vin in vins])
    actor = Actor.user(uuid.uuid4(), Role.CREDIT_OFFICER)
    command = IngestCommand(world.facility.facility_id, kind, AS_OF, "t.csv", data, actor)
    submission = (
        IngestTape(world.units, TapeFileReader(), AccessPolicy(), world.runtime)
        .execute(command)
        .submission_id
    )
    CleanTape(world.units, world.runtime).run(submission)
    return submission


def checks(world: World, outside: MemoryOutsideRecords, chunk: int = 2) -> RunLocalChecks:
    sources = OutsideRecordsClients(outside, outside, outside)
    return RunLocalChecks(world.units, sources, LocalCheckRules(CheckWindows()), world.runtime, chunk)


def states(world: World) -> dict[str, LoanState]:
    return {loan.terms.vin.value: loan.state for loan in world.store.state.loans.values()}


def test_every_accepted_loan_gets_four_results_and_a_failure_holds_it(world: World) -> None:
    submission = cleaned(world, VINS)
    checks(world, outside_for(VINS, held=VINS[2])).run(submission)
    state = world.store.state
    assert state.tapes[submission].status is TapeStatus.CHECKED
    assert all(len(results) == 4 for results in state.checks.values()) and len(state.checks) == 4
    assert states(world) == {
        VINS[0]: LoanState.PENDING,
        VINS[1]: LoanState.PENDING,
        VINS[2]: LoanState.HELD_LOCAL,
        VINS[3]: LoanState.PENDING,
    }
    (held,) = [c for c in world.audit(AuditAction.LOAN_STATE_CHANGED) if c.detail["to"] == "HELD_LOCAL"]
    assert held.detail["failed_checks"] == [CheckType.TITLE_EXISTS.value], "LIENHOLDER_MATCH is UNKNOWN here"
    assert held.detail["reason_code"] == "LOCAL_CHECK_FAILED" and held.detail["submission_id"] == str(
        submission
    )
    advanced = world.audit(AuditAction.TAPE_STAGE_ADVANCED)[-1]
    assert dict(advanced.detail) == {
        "from": "CLEANED",
        "to": "CHECKED",
        "loans_checked": 4,
        "loans_held_local": 1,
    }
    committed = [fields for _, event, fields in world.log.events if event == "check_chunk_committed"]
    assert committed == [
        {"submission_id": str(submission), "batch_index": 0, "count": 2},
        {"submission_id": str(submission), "batch_index": 1, "count": 2},
    ]


def test_an_outage_leaves_its_chunk_unwritten_and_a_rerun_skips_finished_loans(world: World) -> None:
    submission = cleaned(world, VINS)
    outside = outside_for(VINS)
    outside.down_after = 3 * 3
    with pytest.raises(OutsideRecordsUnavailableError):
        checks(world, outside, chunk=2).run(submission)
    state = world.store.state
    assert len(state.checks) == 2 and state.tapes[submission].status is TapeStatus.CLEANED
    assert set(states(world).values()) == {LoanState.PENDING}
    outside.down_after, outside.calls = None, 0
    checks(world, outside, chunk=2).run(submission)
    assert outside.calls == 2 * 3 and len(world.store.state.checks) == 4
    assert world.store.state.tapes[submission].status is TapeStatus.CHECKED


def test_a_crash_after_a_chunk_resumes_without_checking_a_loan_twice(world: World) -> None:
    submission = cleaned(world, VINS)
    outside = outside_for(VINS, held=VINS[0])
    world.faults.arm(FaultPoint.AFTER_CHECK_CHUNK)
    with pytest.raises(CrashError):
        checks(world, outside, chunk=1).run(submission)
    assert len(world.store.state.checks) == 1 and outside.calls == 3
    checks(world, outside, chunk=1).run(submission)
    assert outside.calls == 4 * 3
    assert world.faults.passed.count(FaultPoint.AFTER_CHECK_CHUNK) == 4
    held = [c for c in world.audit(AuditAction.LOAN_STATE_CHANGED) if c.detail["to"] == "HELD_LOCAL"]
    assert len(held) == 1


def test_a_release_tape_only_advances(world: World) -> None:
    first = cleaned(world, VINS[:1])
    checks(world, outside_for(VINS)).run(first)
    loan = next(iter(world.store.state.loans.values()))
    world.store.state.loans[loan.loan_id] = type(loan)(replace(loan.record, state=LoanState.LOCKED))
    release = cleaned(world, VINS[:1], TapeKind.RELEASE)
    outside = outside_for(VINS)
    checks(world, outside).run(release)
    assert world.store.state.tapes[release].status is TapeStatus.CHECKED and outside.calls == 0


def test_a_submission_in_another_status_is_left_alone(world: World) -> None:
    submission = cleaned(world, VINS[:1])
    outside = outside_for(VINS)
    checks(world, outside).run(submission)
    checks(world, outside).run(submission)
    checks(world, outside).run(uuid.uuid4())
    assert outside.calls == 3


def test_a_missing_facility_is_an_error_not_a_silent_skip(world: World) -> None:
    submission = cleaned(world, VINS[:1])
    del world.store.state.facilities[world.facility.facility_id]
    with pytest.raises(FacilityNotFoundError):
        checks(world, outside_for(VINS)).run(submission)


def test_failed_checks_names_only_fail_outcomes_in_the_fixed_order(world: World) -> None:
    """Decision 48: a tolerated UNKNOWN (first payment not due yet) is never listed as failed."""
    young = AS_OF - timedelta(days=20)
    data = summit_tape([summit_row(origination=young.strftime("%m/%d/%Y"))])
    actor = Actor.user(uuid.uuid4(), Role.ANALYST)
    command = IngestCommand(world.facility.facility_id, TapeKind.PLEDGE, AS_OF, "t.csv", data, actor)
    submission = IngestTape(world.units, TapeFileReader(), AccessPolicy(), world.runtime).execute(command)
    CleanTape(world.units, world.runtime).run(submission.submission_id)
    outside = MemoryOutsideRecords()
    outside.titles[VINS[0]] = TitleRecord("NJ", "CLEAN", "Some Other Lender Inc", None)
    checks(world, outside).run(submission.submission_id)
    (results,) = world.store.state.checks.values()
    outcomes = {r.check_type: r.outcome.value for r in results}
    assert outcomes[CheckType.PAYMENTS_RECEIVED] == "UNKNOWN"
    (held,) = [c for c in world.audit(AuditAction.LOAN_STATE_CHANGED) if c.detail["to"] == "HELD_LOCAL"]
    expected = [kind.value for kind in CheckType if outcomes[kind] == "FAIL"]
    assert held.detail["failed_checks"] == expected
    assert CheckType.PAYMENTS_RECEIVED.value not in expected and CheckType.LIENHOLDER_MATCH.value in expected
