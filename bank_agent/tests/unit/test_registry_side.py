"""Outbox, checkpoints, certificate recovery, the halt rule, review, resubmission and retry, in memory.

SPEC 14, 15.2 to 15.4 and SPEC_DECISIONS 56, 57 and 61, driven through the real use
cases over the in-memory ports and the scripted registry (`MemoryBank`).
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from bank_testkit.memory.bank import MemoryBank, vins
from bank_testkit.memory.registry import Tamper
from bank_testkit.world import CrashError
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.review_alert import ReviewAction
from onepledge_bank_agent.domain.audit import Actor
from onepledge_bank_agent.domain.dependency_errors import (
    RegistryIntegrityBrokenError,
    RegistryRejectedError,
    RegistryUnavailableError,
)
from onepledge_bank_agent.domain.enums import (
    AlertStatus,
    AuditAction,
    BankPosition,
    CaseStatus,
    CheckpointFailure,
    FailureReason,
    LoanState,
    QuarantineReason,
    TapeStatus,
)
from onepledge_bank_agent.domain.errors import (
    AlertTransitionInvalidError,
    ForbiddenError,
    LoanNotResubmittableError,
    NotFoundError,
    OutsideRecordsRejectedError,
    PipelineStageError,
    PositionAlreadyRecordedError,
    TapeNotRetryableError,
    ValidationFailedError,
)
from onepledge_bank_agent.domain.integrity import SectionStatus
from onepledge_bank_agent.domain.registry_records import QuarantinedPayload

pytestmark = pytest.mark.unit


def asset_of(bank: MemoryBank, vin: str) -> str:
    loan = bank.loans()[vin]
    tokens = bank.world.store.state.tokens[loan.loan_id]
    return next(t.value.value for t in tokens if t.kind.value == "ASSET")


def sync(bank: MemoryBank) -> None:
    bank.outbox_sync().run()


# -- certificates, the halt rule and recovery (SPEC_DECISIONS 61, 56) ---------------------------


def test_a_failed_certificate_halts_the_bank_until_a_retry_after_restore_recovers_it() -> None:
    bank = MemoryBank()
    first = bank.upload(vins(1))
    bank.run(first, until=TapeStatus.SUBMITTED)
    (certificate_id,) = bank.registry.certificate_ids()
    bank.registry.tamper(certificate_id, Tamper.BODY)
    assert bank.run(first)[-1].status is TapeStatus.FAILED
    assert bank.tape(first).progress.resume_status is TapeStatus.SUBMITTED
    assert bank.halt_rule().active()

    second = bank.upload(vins(1, start=10))
    decisions = bank.registry.decisions
    run = bank.run(second)[-1]
    assert run.failure is not None and run.failure["failure_code"] == "REGISTRY_INTEGRITY_BROKEN"
    assert bank.registry.decisions == decisions, "nothing was sent"

    fetched = bank.registry.calls["certificate"]
    bank.retry(first)
    assert bank.run(first)[-1].status is TapeStatus.FAILED, "the same bad certificate fails again"
    assert bank.registry.calls["certificate"] == fetched + 1, "the retry fetched it again"
    assert len(bank.world.store.state.certificates) == 1, "and stored nothing new"

    bank.registry.restore(certificate_id)
    bank.retry(first)
    assert bank.run(first)[-1].status is TapeStatus.COMPLETED, (
        "the retry is not blocked by the halt it clears"
    )
    assert not bank.halt_rule().active()
    stored = bank.world.store.state.certificates
    assert [(c.certificate_id, c.verified) for c in stored] == [
        (certificate_id, False),
        (certificate_id, True),
    ]
    report = bank.report()
    assert report.certificates.status is SectionStatus.OK and len(report.certificates.failures) == 1
    bank.retry(second)
    assert bank.run(second)[-1].status is TapeStatus.COMPLETED


def test_the_halt_releases_only_when_every_affected_request_has_a_verified_row() -> None:
    bank = MemoryBank(batch_items=1)
    tape = bank.upload(vins(2))
    bank.run(tape, until=TapeStatus.SUBMITTED)
    first, second = bank.registry.certificate_ids()
    bank.registry.tamper(first, Tamper.SIGNATURE)
    bank.registry.tamper(second, Tamper.SIGNATURE)
    bank.run(tape)
    bank.registry.restore(first)
    bank.retry(tape)
    assert bank.run(tape)[-1].status is TapeStatus.FAILED
    assert bank.halt_rule().active(), "one request is still unproven"
    bank.registry.restore(second)
    bank.retry(tape)
    assert bank.run(tape)[-1].status is TapeStatus.COMPLETED and not bank.halt_rule().active()


def test_a_crash_between_an_answer_and_its_certificate_does_not_halt_and_resumes() -> None:
    bank = MemoryBank()
    tape = bank.upload(vins(1))
    bank.run(tape, until=TapeStatus.SUBMITTED)
    bank.world.faults.arm(FaultPoint.AFTER_CERTIFICATE_STORED)
    bank.registry.fail("certificate", RegistryUnavailableError(None, FailureReason.CONNECTION_FAILED))
    run = bank.run(tape)[-1]
    assert run.status is TapeStatus.SUBMITTED and bank.world.store.state.certificates == []
    assert not bank.halt_rule().active(), "a request with no certificate row is not unproven"
    bank.world.faults.armed = None
    bank.world.clock.advance(10)
    assert bank.run(tape)[-1].status is TapeStatus.COMPLETED


def test_a_crash_after_the_answer_is_stored_leaves_no_certificate_row_no_halt_and_resumes() -> None:
    """SPEC_DECISIONS 68.6: the answer is committed, the process dies before the certify stage."""
    bank = MemoryBank()
    tape = bank.upload(vins(2))
    bank.run(tape, until=TapeStatus.TOKENIZED)
    bank.world.faults.arm(FaultPoint.AFTER_RESPONSE_STORED)
    with pytest.raises(CrashError):
        bank.run(tape)
    state = bank.world.store.state
    assert [r.response_status for r in state.requests.values()] == [201], "the answer is stored"
    assert state.certificates == [] and bank.registry.calls["certificate"] == 0
    assert not bank.halt_rule().active(), "a request with no certificate row is not unproven"
    with bank.world.units() as unit:
        assert (unit.evidence.certificates.expected(), unit.evidence.certificates.any_unproven()) == (
            1,
            False,
        )
    bank.world.faults.armed = None
    sent, decisions = bank.registry.calls["submit"], bank.registry.decisions
    assert (sent, decisions) == (1, 1)
    assert bank.run(tape)[-1].status is TapeStatus.COMPLETED
    assert (bank.registry.calls["submit"], bank.registry.decisions) == (sent, decisions), "nothing sent again"
    assert bank.registry.calls["certificate"] == 1
    assert [c.verified for c in bank.world.store.state.certificates] == [True]
    assert not bank.halt_rule().active()


def test_a_certificate_stored_then_a_crash_completes_on_resume_without_a_second_fetch() -> None:
    bank = MemoryBank()
    tape = bank.upload(vins(1))
    bank.run(tape, until=TapeStatus.SUBMITTED)
    bank.world.faults.arm(FaultPoint.AFTER_CERTIFICATE_STORED)
    with pytest.raises(CrashError):
        bank.run(tape)
    fetched = bank.registry.calls["certificate"]
    assert bank.run(tape)[-1].status is TapeStatus.COMPLETED
    assert bank.registry.calls["certificate"] == fetched, "a verified certificate is never fetched again"


@pytest.mark.parametrize("condition", ["checkpoint", "quarantined checkpoint"])
def test_each_halt_condition_stops_a_submission_before_anything_is_sent(condition: str) -> None:
    bank = MemoryBank()
    bank.registry.append_event("GENESIS")
    if condition == "checkpoint":
        bank.registry.checkpoint(bad_signature=True)
    else:
        payload = QuarantinedPayload(QuarantineReason.SCHEMA_MISMATCH, {"body": "x"}, "a" * 64)
        bank.registry.deliver("CHECKPOINT", payload)
    sync(bank)
    tape = bank.upload(vins(1))
    run = bank.run(tape)[-1]
    assert run.failure is not None
    assert (run.failure["failure_code"], run.failure["reason"]) == (
        "REGISTRY_INTEGRITY_BROKEN",
        "INTEGRITY_HALT",
    )
    assert bank.registry.decisions == 0
    off = MemoryBank(bank.world, halt=False)
    off.registry = bank.registry
    off.retry(tape)
    assert off.run(tape)[-1].status is TapeStatus.COMPLETED
    assert off.startup().run("worker") and bank.world.audit(AuditAction.HALT_RULE_DISABLED)
    assert not bank.startup().run("worker"), "nothing is audited with the rule on"


# -- outbox and checkpoints (SPEC 14.1, 15.3, SPEC_DECISIONS 57) ---------------------------------


def test_alerts_and_case_updates_are_applied_once_and_acknowledged() -> None:
    bank = MemoryBank()
    car = vins(1)[0]
    bank.pledge([car])
    case_id = bank.registry.alert(asset_of(bank, car))
    bank.registry.alert("e" * 64)
    result = bank.outbox_sync().sync()
    assert (result.messages_processed, result.alerts_created) == (2, 1)
    assert len(bank.world.audit(AuditAction.ALERT_UNMATCHED)) == 1
    (alert,) = bank.alerts()
    assert (alert.status, alert.record.registry_case_id) == (AlertStatus.NEW, case_id)
    bank.registry.case_update(case_id, CaseStatus.CLEARED_DATA_ERROR)
    bank.registry.case_update(case_id, CaseStatus.CLEARED_DATA_ERROR)
    assert bank.outbox_sync().sync().case_updates_applied == 1
    (alert,) = bank.alerts()
    assert (alert.status, alert.record.case_status) == (AlertStatus.CLEARED, CaseStatus.CLEARED_DATA_ERROR)
    assert bank.registry.pending() == []


def test_an_unknown_type_is_ignored_and_a_known_bad_payload_is_quarantined_once() -> None:
    bank = MemoryBank()
    bank.registry.deliver("MARKET_NEWS", None)
    payload = QuarantinedPayload(QuarantineReason.SCHEMA_MISMATCH, {"asset_token": "X"}, "b" * 64)
    message = bank.registry.deliver("ALERT", payload)
    bank.world.faults.arm(FaultPoint.BETWEEN_OUTBOX_COMMIT_AND_ACK, skip=1)
    with pytest.raises(CrashError):
        bank.outbox_sync().sync()
    bank.outbox_sync().sync()
    (held,) = bank.world.store.state.quarantined
    assert (held.message_id, held.message_type, held.quarantined.reason) == (
        message.message_id,
        "ALERT",
        QuarantineReason.SCHEMA_MISMATCH,
    )
    (audited,) = bank.world.audit(AuditAction.REGISTRY_MESSAGE_QUARANTINED)
    assert dict(audited.detail) == {
        "message_id": str(message.message_id), "message_type": "ALERT", "reason": "SCHEMA_MISMATCH",
        "payload_sha256": "b" * 64,
    }  # fmt: skip
    assert audited.object_id == held.quarantine_id, "the audit entry names the quarantine row"
    assert audited.outcome.value == "FAILURE"
    assert len(bank.world.audit(AuditAction.REGISTRY_MESSAGE_IGNORED)) == 1
    report = bank.report()
    assert (report.registry_messages.status, report.registry_messages.quarantined) == (
        SectionStatus.BROKEN,
        1,
    )
    assert not bank.halt_rule().active(), "only a quarantined CHECKPOINT halts"


def test_a_good_checkpoint_verifies_and_each_failure_reason_is_stored_and_acknowledged() -> None:
    bank = MemoryBank()
    for _ in range(3):
        bank.registry.append_event("E")
    good = bank.registry.checkpoint()
    sync(bank)
    bank.registry.checkpoint(key_id="registry-unknown-9")
    bank.registry.checkpoint(bad_signature=True)
    bank.registry.checkpoint(checkpoint_seq=good.checkpoint_seq, head_hash="f" * 64)
    bank.registry.checkpoint(checkpoint_seq=good.checkpoint_seq + 10, last_event_seq=good.last_event_seq - 1)
    bank.registry.checkpoint(checkpoint_seq=good.checkpoint_seq + 11, head_hash="e" * 64)
    bank.registry.append_event("E")
    bank.registry.append_event("E")
    bank.registry.rewrite_event(4)
    bank.registry.checkpoint(checkpoint_seq=good.checkpoint_seq + 12)
    result = bank.outbox_sync().sync()
    assert (result.checkpoints_verified, result.checkpoints_failed) == (0, 6)
    failures = [c.failure for c in bank.world.store.state.checkpoints]
    assert failures == [
        None,
        CheckpointFailure.UNKNOWN_SIGNING_KEY,
        CheckpointFailure.BAD_SIGNATURE,
        CheckpointFailure.FORK,
        CheckpointFailure.NON_MONOTONIC,
        CheckpointFailure.HEAD_MISMATCH,
        CheckpointFailure.CHAIN_BREAK,
    ]
    assert bank.registry.pending() == []
    assert len(bank.world.audit(AuditAction.CHECKPOINT_REJECTED)) == 6 and bank.halt_rule().active()
    assert bank.report().registry_chain.status is SectionStatus.BROKEN


def test_a_checkpoint_is_not_stored_while_its_headers_cannot_be_read() -> None:
    bank = MemoryBank()
    bank.registry.append_event("E")
    bank.registry.checkpoint()
    bank.registry.fail("event_headers", RegistryUnavailableError(503, FailureReason.SERVER_ERROR))
    bank.outbox_sync().run()
    assert bank.world.store.state.checkpoints == [] and len(bank.registry.pending()) == 1
    events = [event for _, event, _ in bank.world.log.events]
    assert "outbox_sync_interrupted" in events
    assert bank.outbox_sync().sync().checkpoints_verified == 1


def test_an_outbox_outage_ends_the_periodic_sync_quietly_and_an_asked_sync_reports_it() -> None:
    bank = MemoryBank()
    bank.registry.fail("outbox", RegistryUnavailableError(503, FailureReason.SERVER_ERROR), times=2)
    bank.outbox_sync().run()
    with pytest.raises(RegistryUnavailableError):
        bank.outbox_sync().requested(bank.requester())
    assert bank.world.audit(AuditAction.REGISTRY_SYNC_REQUESTED) == []
    assert bank.outbox_sync().requested(bank.requester()).messages_processed == 0
    assert len(bank.world.audit(AuditAction.REGISTRY_SYNC_REQUESTED)) == 1


def test_a_sync_that_finds_the_outbox_lock_held_does_nothing() -> None:
    bank = MemoryBank()
    bank.registry.deliver("MARKET_NEWS", None)
    bank.outbox_lock.busy = True
    assert bank.outbox_sync().sync().skipped is True
    assert len(bank.registry.pending()) == 1


# -- review, positions and resubmission (SPEC 14.5) ----------------------------------------------


def conflict(bank: MemoryBank) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A pledged loan in CONFLICT with its claimant alert: (loan, alert, case)."""
    car = vins(1, start=40)[0]
    tape = bank.upload([car])
    bank.run(tape, until=TapeStatus.TOKENIZED)
    bank.registry.held_elsewhere.add(asset_of(bank, car))
    bank.run(tape)
    (alert,) = bank.alerts()
    return bank.loans()[car].loan_id, alert.record.alert_id, alert.record.registry_case_id


def test_a_position_is_reviewed_then_recorded_and_a_cleared_loan_is_resubmitted_and_locked() -> None:
    bank = MemoryBank()
    loan_id, alert_id, case_id = conflict(bank)
    with pytest.raises(LoanNotResubmittableError):
        bank.resubmit(loan_id)
    with pytest.raises(AlertTransitionInvalidError):
        bank.review(alert_id, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)
    with pytest.raises(ValidationFailedError):
        bank.review(alert_id, ReviewAction.START_REVIEW, BankPosition.DATA_ERROR)
    assert bank.review(alert_id, ReviewAction.START_REVIEW).status is AlertStatus.REVIEWING
    with pytest.raises(ValidationFailedError):
        bank.review(alert_id, ReviewAction.SUBMIT_POSITION)
    bank.registry.case_outcomes[case_id] = CaseStatus.CLEARED_DATA_ERROR
    cleared = bank.review(alert_id, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)
    assert (cleared.status, cleared.record.case_status) == (
        AlertStatus.CLEARED,
        CaseStatus.CLEARED_DATA_ERROR,
    )
    assert bank.registry.positions == {case_id: BankPosition.DATA_ERROR}
    bank.registry.held_elsewhere.clear()
    loan = bank.resubmit(loan_id)
    assert loan.state is LoanState.LOCKED
    (resubmitted,) = bank.world.audit(AuditAction.LOAN_RESUBMITTED)
    assert resubmitted.detail["certificate_verified"] is True
    with pytest.raises(LoanNotResubmittableError):
        bank.resubmit(loan_id)


def test_an_open_case_keeps_the_alert_reviewing_and_a_changed_position_is_refused() -> None:
    bank = MemoryBank()
    _, alert_id, _ = conflict(bank)
    bank.review(alert_id, ReviewAction.START_REVIEW)
    waiting = bank.review(alert_id, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)
    assert (waiting.status, waiting.record.review.bank_position) == (
        AlertStatus.REVIEWING,
        BankPosition.DATA_ERROR,
    )
    with pytest.raises(PositionAlreadyRecordedError):
        bank.review(alert_id, ReviewAction.SUBMIT_POSITION, BankPosition.CONFIRMED_DOUBLE_PLEDGE)


def test_a_case_already_resolved_runs_a_sync_and_returns_the_alert_as_it_then_stands() -> None:
    bank = MemoryBank()
    _, alert_id, case_id = conflict(bank)
    bank.review(alert_id, ReviewAction.START_REVIEW)
    bank.registry.resolved.add(case_id)
    bank.registry.case_update(case_id, CaseStatus.CONFIRMED)
    alert = bank.review(alert_id, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)
    assert (alert.status, alert.record.case_status) == (AlertStatus.ESCALATED, CaseStatus.CONFIRMED)


@pytest.mark.parametrize(
    "error",
    [
        RegistryUnavailableError(503, FailureReason.SERVER_ERROR),
        RegistryRejectedError(403, FailureReason.FORBIDDEN),
    ],
)
def test_a_dependency_failure_on_a_position_changes_nothing(error: PipelineStageError) -> None:
    bank = MemoryBank()
    _, alert_id, _ = conflict(bank)
    bank.review(alert_id, ReviewAction.START_REVIEW)
    bank.registry.fail("record_position", error)
    with pytest.raises(type(error)):
        bank.review(alert_id, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)
    (alert,) = bank.alerts()
    assert (alert.status, alert.record.review.bank_position) == (AlertStatus.REVIEWING, None)


def test_the_halt_rule_refuses_a_position_and_a_resubmission_and_sends_nothing() -> None:
    bank = MemoryBank()
    loan_id, alert_id, _ = conflict(bank)
    bank.review(alert_id, ReviewAction.START_REVIEW)
    bank.registry.append_event("E")
    bank.registry.checkpoint(bad_signature=True)
    sync(bank)
    with pytest.raises(RegistryIntegrityBrokenError):
        bank.review(alert_id, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)
    with pytest.raises(RegistryIntegrityBrokenError):
        bank.resubmit(loan_id)
    assert bank.registry.calls["record_position"] == 0 and bank.registry.positions == {}


def test_an_analyst_may_start_a_review_but_not_record_a_position_and_the_refusal_is_audited() -> None:
    bank = MemoryBank()
    _, alert_id, _ = conflict(bank)
    bank.review(alert_id, ReviewAction.START_REVIEW, actor=bank.analyst)
    with pytest.raises(ForbiddenError):
        bank.review(alert_id, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR, actor=bank.analyst)
    with pytest.raises(ForbiddenError):
        bank.review(alert_id, ReviewAction.START_REVIEW, actor=Actor.system())
    assert len(bank.world.audit(AuditAction.ACCESS_DENIED)) == 2
    with pytest.raises(NotFoundError):
        bank.review(uuid.uuid4(), ReviewAction.START_REVIEW)
    with pytest.raises(NotFoundError):
        bank.resubmit(uuid.uuid4())


# -- retry rules (SPEC 13.8, SPEC_DECISIONS 26, 61) -----------------------------------------------


def test_retry_resumes_a_failed_tape_and_refuses_a_tape_that_is_not_failed() -> None:
    bank = MemoryBank()
    tape = bank.upload(vins(1))
    with pytest.raises(TapeNotRetryableError):
        bank.retry(tape)
    bank.outside.failures.append(OutsideRecordsRejectedError(403, FailureReason.FORBIDDEN))
    bank.run(tape)
    assert bank.tape(tape).status is TapeStatus.FAILED
    resumed = bank.retry(tape)
    assert resumed.status is TapeStatus.CLEANED and resumed.progress.failure_code is None
    (audited,) = bank.world.audit(AuditAction.TAPE_RETRY_REQUESTED)
    assert (audited.detail["failure_code"], audited.detail["resume_status"]) == (
        "OUTSIDE_RECORDS_REJECTED",
        "CLEANED",
    )
    with pytest.raises(ForbiddenError):
        bank.retry(tape, actor=bank.analyst)
    assert bank.run(tape)[-1].status is TapeStatus.COMPLETED


def test_a_retry_of_an_unknown_submission_is_not_found_and_audits_nothing() -> None:
    bank = MemoryBank()
    stranger = bank.upload(vins(1))
    bank.world.store.state.tapes.clear()
    with pytest.raises(NotFoundError):
        bank.retry(stranger)
    assert bank.world.audit(AuditAction.TAPE_RETRY_REQUESTED) == []


class StopOnFirstCommit:
    """A stop requested once the first outbox message is committed, as a SIGTERM arriving then would."""

    def __init__(self) -> None:
        self.requested = False

    def reached(self, point: FaultPoint) -> None:
        """Request the stop at the commit before the acknowledgement."""
        if point is FaultPoint.BETWEEN_OUTBOX_COMMIT_AND_ACK:
            self.requested = True

    def is_set(self) -> bool:
        """True once requested."""
        return self.requested

    def wait(self, seconds: float) -> bool:
        """No sleeping in a unit test."""
        return self.requested


def test_a_stop_during_a_sync_ends_it_after_the_message_in_hand_is_acknowledged() -> None:
    """SPEC_DECISIONS 51: the message being processed is committed and acknowledged, the rest wait."""
    bank = MemoryBank()
    first, *rest = [bank.registry.deliver(f"FUTURE_TYPE_{n}", None) for n in range(3)]
    stop = StopOnFirstCommit()
    bank.world.runtime = replace(bank.world.runtime, faults=stop, stop=stop)
    result = bank.outbox_sync().sync()
    assert result.messages_processed == 1
    assert bank.registry.pending() == rest and first not in bank.registry.pending()
    assert len(bank.world.audit(AuditAction.REGISTRY_MESSAGE_IGNORED)) == 1
