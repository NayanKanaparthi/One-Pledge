"""The tokenise, submit and certify stages over the in-memory ports and scripted fakes (SPEC 13.3 to 13.6).

Every test drives the real use cases as the composition root wires them (`MemoryBank`):
the registry answers, the key holders and the certificates are scripted, nothing else
is replaced. Assertions are on stored state, audit entries and what the fakes received.
"""

from __future__ import annotations

import pytest

from bank_testkit.memory.bank import MemoryBank, vins
from bank_testkit.memory.registry import Tamper
from onepledge_bank_agent.application.advance_submission import StageOutcome
from onepledge_bank_agent.domain.dependency_errors import (
    KeyHolderRejectedError,
    KeyHolderUnavailableError,
    RegistryRejectedError,
    RegistryUnavailableError,
)
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    CertificateFailure,
    FailureCode,
    FailureReason,
    LoanState,
    RegistryItemResult,
    TapeKind,
    TapeStatus,
)
from onepledge_bank_agent.domain.errors import TapeNotRetryableError
from onepledge_bank_agent.domain.tokenization import TOKENS_PER_LOAN

pytestmark = pytest.mark.unit


@pytest.fixture
def bank() -> MemoryBank:
    return MemoryBank()


def asset_tokens(bank: MemoryBank) -> dict[str, str]:
    state = bank.world.store.state
    by_loan = {loan.loan_id: vin for vin, loan in bank.loans().items()}
    return {
        by_loan[loan_id]: token.value.value
        for loan_id, tokens in state.tokens.items()
        for token in tokens
        if token.kind.value == "ASSET"
    }


def statuses(bank: MemoryBank) -> dict[str, LoanState]:
    return {vin: loan.state for vin, loan in bank.loans().items()}


def test_a_pledge_is_tokenised_submitted_in_batches_and_certified(bank: MemoryBank) -> None:
    cars = vins(5)
    submission = bank.pledge(cars)
    assert bank.tape(submission).status is TapeStatus.COMPLETED
    assert set(statuses(bank).values()) == {LoanState.LOCKED}
    state = bank.world.store.state
    assert all(len(tokens) == TOKENS_PER_LOAN for tokens in state.tokens.values())
    requests = sorted(state.requests.values(), key=lambda r: r.batch_index)
    assert [r.item_count for r in requests] == [2, 2, 1] and [r.attempts for r in requests] == [1, 1, 1]
    assert len({r.idempotency_key.value for r in requests}) == 3
    assert [c.verified for c in state.certificates] == [True, True, True]
    assert bank.registry.decisions == 3 and len(bank.registry.locks) == 5
    assert bank.holders[0].calls == [16, 16, 8] == bank.holders[1].calls, "chunks of two loans, eight each"
    assert len(bank.world.audit(AuditAction.CERTIFICATE_VERIFIED)) == 3


def test_re_running_a_finished_stage_sends_nothing_again(bank: MemoryBank) -> None:
    submission = bank.pledge(vins(3))
    calls = dict(bank.registry.calls)
    advance = bank.advance()
    assert advance.advance(submission.submission_id).outcome is StageOutcome.NOTHING_TO_DO
    stages = bank.stages()
    for stage in stages[2:]:
        stage.run(submission.submission_id)
    assert dict(bank.registry.calls) == calls and bank.holders[0].calls == [16, 8]


def test_the_same_loan_gets_the_same_tokens_at_the_same_key_versions() -> None:
    first, second = MemoryBank(), MemoryBank()
    for bank in (first, second):
        bank.pledge(vins(2))
    assert asset_tokens(first) == asset_tokens(second)
    changed = MemoryBank()
    changed.holders[1].version = 2
    changed.pledge(vins(2))
    assert set(asset_tokens(changed).values()).isdisjoint(asset_tokens(first).values())


def test_a_key_holder_outage_stores_nothing_of_the_chunk_and_backs_off(bank: MemoryBank) -> None:
    submission = bank.upload(vins(3))
    bank.holders[1].failures.append(KeyHolderUnavailableError("key_holder_2", 503))
    runs = bank.run(submission)
    assert runs[-1].outcome is StageOutcome.RETRY_SCHEDULED and runs[-1].status is TapeStatus.CHECKED
    assert runs[-1].failure is not None and runs[-1].failure["dependency"] == "key_holder_2"
    assert bank.world.store.state.tokens == {}, "holder 1 answered, but nothing of the chunk is stored"
    bank.world.clock.advance(10)
    assert bank.run(submission)[-1].status is TapeStatus.COMPLETED


def test_a_key_holder_refusal_fails_the_tape_at_once_with_its_reason(bank: MemoryBank) -> None:
    submission = bank.upload(vins(1))
    bank.holders[0].failures.append(KeyHolderRejectedError("key_holder_1", 403, FailureReason.FORBIDDEN))
    run = bank.run(submission)[-1]
    assert run.outcome is StageOutcome.FAILED
    assert run.failure == {
        "stage": "TOKENIZED", "failure_code": "KEY_HOLDER_REJECTED", "attempts": 0,
        "dependency": "key_holder_1", "http_status": 403, "reason": "FORBIDDEN",
    }  # fmt: skip


def test_an_element_that_does_not_unblind_is_a_protocol_violation_and_stores_nothing(
    bank: MemoryBank,
) -> None:
    submission = bank.upload(vins(1))
    bank.holders[1].corrupt = True
    run = bank.run(submission)[-1]
    assert (run.status, run.failure and run.failure["reason"]) == (TapeStatus.FAILED, "PROTOCOL_VIOLATION")
    assert bank.world.store.state.tokens == {}


def test_a_key_version_change_between_chunks_fails_with_key_version_mismatch(bank: MemoryBank) -> None:
    submission = bank.upload(vins(4))
    stages = bank.stages()
    stages[0].run(submission.submission_id)
    stages[1].run(submission.submission_id)

    bank.holders[0].rotate_after = 1
    run = bank.advance().advance(submission.submission_id)
    assert run.status is TapeStatus.FAILED and run.failure is not None
    assert (run.failure["failure_code"], run.failure["reason"]) == (
        "KEY_VERSION_MISMATCH",
        "KEY_VERSION_MISMATCH",
    )
    tokenised = bank.world.store.state.tokens
    assert len(tokenised) == 2, "the first chunk stays stored"
    with pytest.raises(TapeNotRetryableError):
        bank.retry(submission)


def test_a_registry_outage_on_the_third_batch_keeps_the_first_two_and_resends_only_the_third(
    bank: MemoryBank,
) -> None:
    submission = bank.upload(vins(5))
    bank.run(submission, until=TapeStatus.TOKENIZED)
    bank.registry.fail("submit", RegistryUnavailableError(503, FailureReason.SERVER_ERROR), skip=2, times=2)
    first = bank.advance().advance(submission.submission_id)
    assert first.outcome is StageOutcome.RETRY_SCHEDULED
    assert bank.registry.decisions == 2, "the first two batches were decided"
    bank.world.clock.advance(10)
    second = bank.advance().advance(submission.submission_id)
    assert second.outcome is StageOutcome.RETRY_SCHEDULED, "the second scripted outage"
    bank.world.clock.advance(10)
    assert bank.run(submission)[-1].status is TapeStatus.COMPLETED
    state = bank.world.store.state
    assert sorted(r.attempts for r in state.requests.values()) == [1, 1, 3]
    assert bank.registry.decisions == 3


def test_a_permanent_refusal_on_a_later_batch_keeps_the_answered_batches_applied(bank: MemoryBank) -> None:
    submission = bank.upload(vins(5))
    bank.run(submission, until=TapeStatus.TOKENIZED)
    bank.registry.fail("submit", RegistryRejectedError(403, FailureReason.FORBIDDEN), skip=2)
    advance = bank.advance()
    run = advance.advance(submission.submission_id)
    assert run.status is TapeStatus.FAILED and run.failure is not None
    assert (run.failure["failure_code"], run.failure["http_status"]) == ("REGISTRY_REJECTED_REQUEST", 403)
    locked = [vin for vin, state in statuses(bank).items() if state is LoanState.LOCKED]
    assert len(locked) == 4, "two answered batches applied"
    assert bank.tape(submission).progress.resume_status is TapeStatus.TOKENIZED


def test_a_lost_answer_is_replayed_and_applied_once(bank: MemoryBank) -> None:
    submission = bank.upload(vins(2))
    bank.run(submission, until=TapeStatus.TOKENIZED)
    bank.registry.fail("submit", RegistryUnavailableError(503, FailureReason.SERVER_ERROR), after=True)
    assert bank.advance().advance(submission.submission_id).outcome is StageOutcome.RETRY_SCHEDULED
    assert bank.registry.decisions == 1, "the registry decided before the answer was lost"
    bank.world.clock.advance(10)
    assert bank.run(submission)[-1].status is TapeStatus.COMPLETED
    (request,) = bank.world.store.state.requests.values()
    assert (request.attempts, request.response_status) == (2, 200), "counted every send; stored the replay"
    assert bank.registry.decisions == 1
    changed = [d for d in bank.world.audit(AuditAction.LOAN_STATE_CHANGED) if d.detail.get("to") == "LOCKED"]
    assert len(changed) == 2, "each loan locked once"


def test_an_answer_about_other_asset_codes_is_refused_and_applies_nothing(bank: MemoryBank) -> None:
    submission = bank.upload(vins(1))
    bank.run(submission, until=TapeStatus.TOKENIZED)
    bank.registry.mismatch = True
    run = bank.advance().advance(submission.submission_id)
    assert run.status is TapeStatus.FAILED and run.failure is not None
    assert (run.failure["failure_code"], run.failure["reason"]) == (
        "REGISTRY_REJECTED_REQUEST",
        "MALFORMED_RESPONSE",
    )
    assert statuses(bank) == {vins(1)[0]: LoanState.PENDING}


def test_a_key_version_the_registry_refuses_fails_the_tape_for_good(bank: MemoryBank) -> None:
    submission = bank.upload(vins(1))
    bank.run(submission, until=TapeStatus.TOKENIZED)
    bank.registry.refuse_key_version = True
    run = bank.advance().advance(submission.submission_id)
    assert run.status is TapeStatus.FAILED
    assert bank.tape(submission).progress.failure_code is FailureCode.KEY_VERSION_MISMATCH


def test_each_registry_result_moves_its_loan_as_spec_13_5_says(bank: MemoryBank) -> None:
    cars = vins(4)
    submission = bank.upload(cars)
    bank.run(submission, until=TapeStatus.TOKENIZED)
    tokens = asset_tokens(bank)
    bank.registry.held_elsewhere.add(tokens[cars[1]])
    bank.registry.rejects[tokens[cars[2]]] = "CAP_EXCEEDED"
    bank.registry.unknown.add(tokens[cars[3]])
    assert bank.run(submission)[-1].status is TapeStatus.COMPLETED
    assert statuses(bank) == {
        cars[0]: LoanState.LOCKED,
        cars[1]: LoanState.CONFLICT,
        cars[2]: LoanState.PENDING,
        cars[3]: LoanState.PENDING,
    }
    items = [i for items in bank.world.store.state.request_items.values() for i in items]
    assert sorted(i.result.value for i in items if i.result) == ["CONFLICT", "LOCKED", "REJECTED", "REJECTED"]
    rejected = {d.detail["reason"] for d in bank.world.audit(AuditAction.REGISTRY_ITEM_REJECTED)}
    assert rejected == {"CAP_EXCEEDED", "UNKNOWN_RESULT"}
    (alert,) = bank.alerts()
    assert (
        alert.record.match.role.value == "CLAIMANT" and alert.record.loan_id == bank.loans()[cars[1]].loan_id
    )
    assert len(bank.world.audit(AuditAction.CERTIFICATE_VERIFIED)) == 2


def test_a_release_releases_the_locked_loans_and_reports_one_it_does_not_hold(bank: MemoryBank) -> None:
    cars = vins(2)
    bank.pledge(cars)
    loans = bank.loans()
    release = bank.release([loans[cars[0]].loan_id])
    assert release.facts.kind is TapeKind.RELEASE
    assert bank.run(release)[-1].status is TapeStatus.COMPLETED
    assert statuses(bank) == {cars[0]: LoanState.RELEASED, cars[1]: LoanState.LOCKED}
    assert tokens_sent_for_release(bank) == 1
    releases = [r for r in bank.world.store.state.requests.values() if r.kind is TapeKind.RELEASE]
    assert [r.item_count for r in releases] == [1]
    items = bank.world.store.state.request_items[releases[0].request_id]
    assert [i.result for i in items] == [RegistryItemResult.RELEASED]


def tokens_sent_for_release(bank: MemoryBank) -> int:
    return sum(1 for r in bank.world.store.state.requests.values() if r.kind is TapeKind.RELEASE)


@pytest.mark.parametrize(
    ("how", "reason"),
    [
        (Tamper.BODY, CertificateFailure.BODY_HASH_MISMATCH),
        (Tamper.SIGNATURE, CertificateFailure.BAD_SIGNATURE),
        (Tamper.KEY, CertificateFailure.UNKNOWN_SIGNING_KEY),
        (Tamper.CONTENT, CertificateFailure.CONTENT_MISMATCH),
    ],
)
def test_each_certificate_reason_fails_the_tape_keeps_the_loans_and_stores_the_evidence(
    how: Tamper, reason: CertificateFailure
) -> None:
    bank = MemoryBank(halt=False)
    submission = bank.upload(vins(1))
    bank.run(submission, until=TapeStatus.SUBMITTED)
    (certificate_id,) = bank.registry.certificate_ids()
    bank.registry.tamper(certificate_id, how)
    run = bank.run(submission)[-1]
    assert run.status is TapeStatus.FAILED and run.failure is not None
    assert (run.failure["failure_code"], run.failure["reason"]) == (
        "CERTIFICATE_INVALID",
        "CERTIFICATE_INVALID",
    )
    assert set(statuses(bank).values()) == {LoanState.LOCKED}, "the loans keep what the registry said"
    (stored,) = bank.world.store.state.certificates
    assert (stored.failure, stored.verified) == (reason, False)
    rejected = bank.world.audit(AuditAction.CERTIFICATE_REJECTED)
    assert [d.detail["failure_reason"] for d in rejected] == [reason.value]
    report = bank.report()
    assert report.certificates.status.value == "BROKEN" and report.certificates.verified == 0
