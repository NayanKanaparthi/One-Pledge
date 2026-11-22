"""`AdvanceSubmission`: dispatch to registered stages and the failure handling of SPEC 13.7."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from bank_testkit.stages import FakeStage, raising
from bank_testkit.tapes import SUMMIT_LAYOUT
from bank_testkit.world import World
from onepledge_bank_agent.application.advance_submission import AdvanceSubmission, StageOutcome
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    FailureCode,
    FailureReason,
    TapeKind,
    TapeSource,
    TapeStatus,
)
from onepledge_bank_agent.domain.errors import (
    OutsideRecordsProtocolError,
    OutsideRecordsRejectedError,
    OutsideRecordsUnavailableError,
    PipelineStageError,
)
from onepledge_bank_agent.domain.tape import TapeFacts, TapeSubmission

pytestmark = pytest.mark.unit


@pytest.fixture
def world() -> World:
    return World()


def received(world: World) -> uuid.UUID:
    submission = uuid.uuid4()
    facts = TapeFacts(
        submission, world.facility.facility_id, TapeKind.PLEDGE, TapeSource.FILE_UPLOAD, "t.csv", "a" * 64,
        "b" * 64, date(2026, 10, 15), 1, uuid.uuid4(), world.clock.now(),
    )  # fmt: skip
    with world.units() as unit:
        unit.tapes.add(TapeSubmission.receive(facts), SUMMIT_LAYOUT)
        unit.commit()
    return submission


def advance(world: World, *stages: FakeStage, max_attempts: int = 3) -> AdvanceSubmission:
    return AdvanceSubmission(world.units, stages, world.runtime, max_attempts)


def status(world: World, submission: uuid.UUID) -> TapeSubmission:
    return world.store.state.tapes[submission]


def test_the_stage_registered_for_the_current_status_runs(world: World) -> None:
    clean, check = FakeStage(world, TapeStatus.CLEANED), FakeStage(world, TapeStatus.CHECKED)
    dispatcher = advance(world, check, clean)
    assert dispatcher.runnable_statuses == {TapeStatus.RECEIVED, TapeStatus.CLEANED}
    submission = received(world)
    first = dispatcher.advance(submission)
    second = dispatcher.advance(submission)
    assert (first.outcome, first.started_at_status, first.status) == (
        StageOutcome.ADVANCED,
        TapeStatus.RECEIVED,
        TapeStatus.CLEANED,
    )
    assert (second.outcome, second.status) == (StageOutcome.ADVANCED, TapeStatus.CHECKED)
    assert dispatcher.advance(submission).outcome is StageOutcome.NOTHING_TO_DO
    assert dispatcher.advance(uuid.uuid4()).outcome is StageOutcome.NOTHING_TO_DO
    assert (clean.runs, check.runs) == (1, 1)


def test_a_transient_failure_backs_off_and_fails_at_the_maximum(world: World) -> None:
    stage = FakeStage(world, TapeStatus.CLEANED, raising(OutsideRecordsUnavailableError()))
    dispatcher = advance(world, stage, max_attempts=3)
    submission = received(world)
    outcomes = [dispatcher.advance(submission).outcome for _ in range(3)]
    assert outcomes == [StageOutcome.RETRY_SCHEDULED, StageOutcome.RETRY_SCHEDULED, StageOutcome.FAILED]
    tape = status(world, submission)
    assert (tape.status, tape.progress.failure_code, tape.progress.failed_stage) == (
        TapeStatus.FAILED, FailureCode.OUTSIDE_RECORDS_UNAVAILABLE, TapeStatus.CLEANED
    )  # fmt: skip
    attempts = world.audit(AuditAction.TAPE_STAGE_ATTEMPT_FAILED)
    assert [a.detail["attempts"] for a in attempts] == [1, 2, 3]
    facts = {"dependency": "outside_records", "http_status": None, "reason": "CONNECTION_FAILED"}
    assert attempts[0].detail == {
        "stage": "CLEANED", "failure_code": "OUTSIDE_RECORDS_UNAVAILABLE", "attempts": 1, **facts,
    }  # fmt: skip
    (failed,) = world.audit(AuditAction.TAPE_FAILED)
    assert failed.detail == {
        "stage": "CLEANED", "failure_code": "OUTSIDE_RECORDS_UNAVAILABLE", "attempts": 3, **facts,
        "resume_status": "RECEIVED",
    }  # fmt: skip
    assert set(failed.detail) - set(attempts[0].detail) == {"resume_status"}, "one shape (SPEC_DECISIONS 58)"


def test_a_scheduled_retry_sets_the_next_attempt_time(world: World) -> None:
    dispatcher = advance(
        world, FakeStage(world, TapeStatus.CLEANED, raising(OutsideRecordsUnavailableError()))
    )
    submission = received(world)
    dispatcher.advance(submission)
    progress = status(world, submission).progress
    assert progress.attempts == 1 and progress.next_attempt_at is not None
    assert (progress.next_attempt_at - world.clock.now()).total_seconds() == 2


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (OutsideRecordsRejectedError(401, FailureReason.UNAUTHENTICATED), "UNAUTHENTICATED"),
        (OutsideRecordsRejectedError(403, FailureReason.FORBIDDEN), "FORBIDDEN"),
        (OutsideRecordsRejectedError(422, FailureReason.REQUEST_REFUSED), "REQUEST_REFUSED"),
        (OutsideRecordsProtocolError(200), "MALFORMED_RESPONSE"),
    ],
)
def test_a_refusal_fails_at_once_with_the_rejected_code_and_its_reason(
    world: World, error: PipelineStageError, reason: str
) -> None:
    submission = received(world)
    run = advance(world, FakeStage(world, TapeStatus.CLEANED, raising(error))).advance(submission)
    assert run.outcome is StageOutcome.FAILED and run.failure is not None
    assert (run.failure["failure_code"], run.failure["reason"]) == ("OUTSIDE_RECORDS_REJECTED", reason)
    assert status(world, submission).progress.failure_code is FailureCode.OUTSIDE_RECORDS_REJECTED
    assert not world.audit(AuditAction.TAPE_STAGE_ATTEMPT_FAILED)
    (failed,) = world.audit(AuditAction.TAPE_FAILED)
    assert dict(failed.detail) == {
        "stage": "CLEANED", "resume_status": "RECEIVED", "failure_code": "OUTSIDE_RECORDS_REJECTED",
        "dependency": "outside_records", "http_status": error.http_status, "reason": reason, "attempts": 0,
    }  # fmt: skip


def test_an_outage_that_reaches_the_attempt_limit_names_the_dependency(world: World) -> None:
    submission = received(world)
    stage = FakeStage(
        world, TapeStatus.CLEANED, raising(OutsideRecordsUnavailableError(503, FailureReason.SERVER_ERROR))
    )
    for _ in range(3):
        advance(world, stage).advance(submission)
        world.clock.advance(60)
    (failed,) = world.audit(AuditAction.TAPE_FAILED)
    assert dict(failed.detail) == {
        "stage": "CLEANED", "resume_status": "RECEIVED", "failure_code": "OUTSIDE_RECORDS_UNAVAILABLE",
        "dependency": "outside_records", "http_status": 503, "reason": "SERVER_ERROR", "attempts": 3,
    }  # fmt: skip


def test_an_unexpected_error_is_logged_without_its_message_and_fails_with_internal_error(
    world: World,
) -> None:
    submission = received(world)
    run = advance(
        world, FakeStage(world, TapeStatus.CLEANED, raising(KeyError("1HGCM82633A004352")))
    ).advance(submission)
    assert run.outcome is StageOutcome.FAILED
    assert status(world, submission).progress.failure_code is FailureCode.INTERNAL_ERROR
    (failed,) = world.audit(AuditAction.TAPE_FAILED)
    assert dict(failed.detail) == {
        "stage": "CLEANED", "failure_code": "INTERNAL_ERROR", "attempts": 0, "dependency": None,
        "http_status": None, "reason": "UNEXPECTED", "resume_status": "RECEIVED",
    }  # fmt: skip
    (level, event, fields), *_ = world.log.events
    assert (level, event, fields["exception_type"]) == ("error", "stage_unexpected_error", "KeyError")
    assert "1HGCM82633A004352" not in repr(world.log.events)


def test_a_failure_after_the_tape_moved_on_changes_nothing(world: World) -> None:
    submission = received(world)

    def advance_elsewhere() -> None:
        with world.units() as unit:
            tape = unit.tapes.get(submission)
            assert tape is not None
            tape.mark_cleaned(world.clock.now())
            unit.tapes.save(tape)
            unit.commit()
        raise OutsideRecordsUnavailableError

    run = advance(world, FakeStage(world, TapeStatus.CLEANED, advance_elsewhere)).advance(submission)
    assert (
        run.outcome is StageOutcome.NOTHING_TO_DO and status(world, submission).status is TapeStatus.CLEANED
    )
