"""The state machines of SPEC 8.3, 8.4 and 8.5: every legal transition succeeds, every other pair raises.

The legal tables are written out here from the SPEC, not imported from the product.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest

from onepledge_bank_agent.domain import BorrowerRef, LenderLoanNumber, LoanTerms, Money, Vin
from onepledge_bank_agent.domain.alert import Alert, AlertIdentity, MatchFacts
from onepledge_bank_agent.domain.enums import (
    AlertRole,
    AlertStatus,
    BankPosition,
    CaseStatus,
    ExplanationSource,
    FailureCode,
    LoanState,
    MatchBasis,
    MatchKind,
    TapeKind,
    TapeSource,
    TapeStatus,
    TokenKind,
)
from onepledge_bank_agent.domain.errors import (
    AlertTransitionInvalidError,
    IllegalTransitionError,
    InvalidValueError,
    PositionAlreadyRecordedError,
)
from onepledge_bank_agent.domain.loan import Loan, LoanDraft, LoanRecord, StateChange
from onepledge_bank_agent.domain.tape import TapeFacts, TapeProgress, TapeSubmission

pytestmark = pytest.mark.unit

AT = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
T = TapeStatus
TAPE_LEGAL = {
    (T.RECEIVED, T.CLEANED), (T.CLEANED, T.CHECKED), (T.CHECKED, T.TOKENIZED), (T.TOKENIZED, T.SUBMITTED),
    (T.SUBMITTED, T.COMPLETED), (T.RECEIVED, T.FAILED), (T.CLEANED, T.FAILED), (T.CHECKED, T.FAILED),
    (T.TOKENIZED, T.FAILED), (T.SUBMITTED, T.FAILED),
}  # fmt: skip
RESUMABLE = [T.RECEIVED, T.CLEANED, T.CHECKED, T.TOKENIZED, T.SUBMITTED]


def facts() -> TapeFacts:
    return TapeFacts(
        uuid.uuid4(), uuid.uuid4(), TapeKind.PLEDGE, TapeSource.FILE_UPLOAD, "t.csv", "a" * 64, "b" * 64,
        date(2026, 10, 15), 3, uuid.uuid4(), AT,
    )  # fmt: skip


def tape_at(
    status: TapeStatus, resume: TapeStatus | None = None, code: FailureCode | None = None
) -> TapeSubmission:
    failed = status is T.FAILED
    progress = TapeProgress(
        status,
        resume if failed else None,
        T.CHECKED if failed else None,
        (code or FailureCode.OUTSIDE_RECORDS_UNAVAILABLE) if failed else None,
        0,
        None,
        AT,
        AT if status is T.COMPLETED else None,
    )
    return TapeSubmission(facts(), progress)


def attempt(tape: TapeSubmission, target: TapeStatus) -> TapeStatus:
    marks: dict[TapeStatus, Callable[[datetime], TapeStatus]] = {
        T.CLEANED: tape.mark_cleaned, T.CHECKED: tape.mark_checked, T.TOKENIZED: tape.mark_tokenized,
        T.SUBMITTED: tape.mark_submitted, T.COMPLETED: tape.mark_completed,
    }  # fmt: skip
    if target in marks:
        marks[target](AT)
    elif target is T.FAILED:
        tape.fail(FailureCode.INTERNAL_ERROR, AT)
    else:
        tape.resume(AT)
    return tape.status


@pytest.mark.parametrize("before", [s for s in TapeStatus if s is not T.FAILED])
@pytest.mark.parametrize("target", list(TapeStatus))
def test_tape_transitions_from_every_unfailed_status(before: TapeStatus, target: TapeStatus) -> None:
    tape = tape_at(before)
    if (before, target) in TAPE_LEGAL:
        assert attempt(tape, target) is target
        if target is T.FAILED:
            assert (
                tape.progress.resume_status is before
                and tape.progress.failure_code is FailureCode.INTERNAL_ERROR
            )
    else:
        with pytest.raises(IllegalTransitionError):
            attempt(tape, target)
        assert tape.status is before


@pytest.mark.parametrize("resume", RESUMABLE)
@pytest.mark.parametrize("target", [s for s in TapeStatus if s is not T.RECEIVED])
def test_a_failed_tape_moves_by_no_stage_method(resume: TapeStatus, target: TapeStatus) -> None:
    tape = tape_at(T.FAILED, resume)
    with pytest.raises(IllegalTransitionError):
        attempt(tape, target)
    assert tape.status is T.FAILED


@pytest.mark.parametrize("resume", RESUMABLE)
def test_a_failed_tape_goes_back_only_to_its_resume_status(resume: TapeStatus) -> None:
    tape = tape_at(T.FAILED, resume)
    assert tape.resume(AT) is resume and tape.status is resume
    assert tape.progress.failure_code is None and tape.progress.attempts == 0
    assert tape.progress.resume_status is None and tape.progress.failed_stage is None


def test_only_a_key_version_mismatch_cannot_be_resumed() -> None:
    """SPEC_DECISIONS 61 amends 26: KEY_VERSION_MISMATCH is the one failure that cannot be retried."""
    with pytest.raises(IllegalTransitionError):
        tape_at(T.FAILED, T.CHECKED, FailureCode.KEY_VERSION_MISMATCH).resume(AT)


@pytest.mark.parametrize("code", sorted(set(FailureCode) - {FailureCode.KEY_VERSION_MISMATCH}))
def test_every_other_failure_can_be_resumed_after_an_operator_fix(code: FailureCode) -> None:
    """SPEC_DECISIONS 26 amends SPEC 13.8 and 61 adds CERTIFICATE_INVALID: retryable after a fix."""
    assert tape_at(T.FAILED, T.CHECKED, code).resume(AT) is T.CHECKED


def test_receive_creates_a_received_tape_and_completion_stamps_the_time() -> None:
    tape = TapeSubmission.receive(facts())
    assert tape.status is T.RECEIVED and tape.next_stage is T.CLEANED
    for target in (T.CLEANED, T.CHECKED, T.TOKENIZED, T.SUBMITTED, T.COMPLETED):
        attempt(tape, target)
    assert tape.progress.completed_at == AT and tape.next_stage is None


def test_transient_failures_back_off_then_fail_at_the_maximum() -> None:
    tape = tape_at(T.CLEANED)
    delays = []
    for _ in range(7):
        assert tape.record_failed_attempt(FailureCode.OUTSIDE_RECORDS_UNAVAILABLE, AT, 8) is False
        assert tape.progress.next_attempt_at is not None
        delays.append((tape.progress.next_attempt_at - AT).total_seconds())
    assert delays == [2, 4, 8, 16, 32, 60, 60] and tape.progress.attempts == 7
    assert tape.record_failed_attempt(FailureCode.OUTSIDE_RECORDS_UNAVAILABLE, AT, 8) is True
    progress = tape.progress
    assert (progress.status, progress.resume_status, progress.failed_stage) == (
        T.FAILED,
        T.CLEANED,
        T.CHECKED,
    )
    assert progress.attempts == 8 and progress.failure_code is FailureCode.OUTSIDE_RECORDS_UNAVAILABLE
    tape.resume(AT)
    assert tape.status is T.CLEANED and tape.progress.attempts == 0
    advanced = tape_at(T.CLEANED)
    advanced.record_failed_attempt(FailureCode.OUTSIDE_RECORDS_UNAVAILABLE, AT, 8)
    advanced.mark_checked(AT)
    assert advanced.progress.attempts == 0 and advanced.progress.next_attempt_at is None


L = LoanState
LOAN_LEGAL = {
    (L.PENDING, "hold_local"), (L.PENDING, "lock"), (L.PENDING, "mark_conflict"),
    (L.HELD_LOCAL, "represent"), (L.CONFLICT, "lock_after_clearance"), (L.LOCKED, "release"),
}  # fmt: skip
LOAN_TARGET = {
    "hold_local": L.HELD_LOCAL, "lock": L.LOCKED, "mark_conflict": L.CONFLICT, "represent": L.PENDING,
    "lock_after_clearance": L.LOCKED, "release": L.RELEASED,
}  # fmt: skip
TERMS = LoanTerms(Vin("1HGCM82633A004352"), BorrowerRef("C1234567"), date(2025, 3, 15), Money(2345017))


def loan_at(state: LoanState) -> Loan:
    record = LoanRecord(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), LenderLoanNumber("SAF-1"), TERMS, Money(100), state, AT, AT
    )
    return Loan(record)


def test_opening_a_loan_is_none_to_pending() -> None:
    draft = LoanDraft(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), LenderLoanNumber("SAF-1"), TERMS, Money(100))
    loan, change = Loan.open(draft, AT)
    assert loan.state is L.PENDING and (change.from_state, change.to_state) == (None, L.PENDING)


@pytest.mark.parametrize("state", list(LoanState))
@pytest.mark.parametrize("method", list(LOAN_TARGET))
def test_loan_transitions(state: LoanState, method: str) -> None:
    loan = loan_at(state)
    action: Callable[[datetime], StateChange | None] = getattr(loan, method)
    if (state, method) in LOAN_LEGAL:
        change = action(AT + timedelta(seconds=1))
        assert loan.state is LOAN_TARGET[method]
        assert change is not None and change.from_state is state
        assert loan.record.updated_at == AT + timedelta(seconds=1)
    elif method == "represent" and state is L.PENDING:
        assert action(AT) is None and loan.state is L.PENDING
    else:
        with pytest.raises(IllegalTransitionError):
            action(AT)
        assert loan.state is state


@pytest.mark.parametrize("state", list(LoanState))
def test_terms_change_only_while_pending_or_held_and_balances_never_after_release(state: LoanState) -> None:
    loan = loan_at(state)
    if state in (L.PENDING, L.HELD_LOCAL):
        loan.replace_terms(TERMS, LenderLoanNumber("SAF-2"), Money(200), AT)
        assert loan.record.lender_loan_number.value == "SAF-2" and loan.state is state
    else:
        with pytest.raises(IllegalTransitionError):
            loan.replace_terms(TERMS, LenderLoanNumber("SAF-2"), Money(200), AT)
    if state is L.RELEASED:
        with pytest.raises(IllegalTransitionError):
            loan.update_balance(Money(50), AT)
    else:
        loan.update_balance(Money(50), AT)
        assert loan.record.current_balance == Money(50)


def test_a_balance_must_be_positive_and_not_above_the_original() -> None:
    loan = loan_at(L.PENDING)
    for balance in (Money(0), Money(2345018)):
        with pytest.raises(InvalidValueError):
            loan.update_balance(balance, AT)


A = AlertStatus
OFFICER = uuid.uuid4()


def alert_at(status: AlertStatus, position: BankPosition | None = None) -> Alert:
    match = MatchFacts(
        AlertRole.CLAIMANT, MatchKind.LIKELY, MatchBasis.ASSET, (TokenKind.ASSET, TokenKind.VIN)
    )
    alert = Alert.raise_new(
        AlertIdentity(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()),
        match,
        ("text", ExplanationSource.TEMPLATE),
        AT,
    )
    if status is not A.NEW:
        alert.start_review(OFFICER, AT)
    if position is not None:
        alert.submit_position(position, OFFICER, AT)
    if status is A.CLEARED:
        alert.apply_case_status(CaseStatus.CLEARED_DATA_ERROR)
    if status is A.ESCALATED:
        alert.apply_case_status(CaseStatus.CONFIRMED)
    assert alert.status is status
    return alert


def test_a_new_alert_is_new_and_open_with_the_remaining_kinds_in_order() -> None:
    alert = alert_at(A.NEW)
    assert alert.record.case_status is CaseStatus.OPEN
    assert alert.record.match.kinds_not_matched == (
        TokenKind.BORROWER, TokenKind.AMOUNT_EXACT, TokenKind.AMOUNT_100, TokenKind.AMOUNT_1000,
        TokenKind.DATE_DAY, TokenKind.DATE_MONTH,
    )  # fmt: skip


@pytest.mark.parametrize("status", list(AlertStatus))
def test_start_review_only_from_new(status: AlertStatus) -> None:
    alert = alert_at(status)
    if status is A.NEW:
        alert.start_review(OFFICER, AT, "checking with the lender")
        assert alert.status is A.REVIEWING and alert.record.review.note == "checking with the lender"
    else:
        with pytest.raises(AlertTransitionInvalidError):
            alert.start_review(OFFICER, AT)


@pytest.mark.parametrize("status", list(AlertStatus))
def test_submit_position_only_while_reviewing_and_only_one_position(status: AlertStatus) -> None:
    alert = alert_at(status)
    if status is not A.REVIEWING:
        with pytest.raises(AlertTransitionInvalidError):
            alert.submit_position(BankPosition.DATA_ERROR, OFFICER, AT)
        return
    alert.submit_position(BankPosition.DATA_ERROR, OFFICER, AT)
    alert.submit_position(BankPosition.DATA_ERROR, OFFICER, AT + timedelta(seconds=5))
    assert alert.status is A.REVIEWING and alert.record.review.position_submitted_at == AT
    with pytest.raises(PositionAlreadyRecordedError):
        alert.submit_position(BankPosition.CONFIRMED_DOUBLE_PLEDGE, OFFICER, AT)


@pytest.mark.parametrize("status", [A.NEW, A.REVIEWING])
@pytest.mark.parametrize(
    ("case", "closed"),
    [
        (CaseStatus.CLEARED_DATA_ERROR, A.CLEARED),
        (CaseStatus.CLEARED_LATE_RELEASE, A.CLEARED),
        (CaseStatus.CONFIRMED, A.ESCALATED),
    ],
)
def test_only_the_case_outcome_closes_an_open_alert(
    status: AlertStatus, case: CaseStatus, closed: AlertStatus
) -> None:
    alert = alert_at(status)
    assert alert.apply_case_status(case) is True
    assert (alert.status, alert.record.case_status) == (closed, case)
    assert alert.apply_case_status(case) is False


def test_terminal_alerts_stay_terminal() -> None:
    cleared = alert_at(A.CLEARED)
    assert cleared.apply_case_status(cleared.record.case_status) is False, "a repeat changes nothing"
    with pytest.raises(IllegalTransitionError):
        cleared.apply_case_status(CaseStatus.CONFIRMED)
    assert cleared.status is A.CLEARED
    assert alert_at(A.NEW).apply_case_status(CaseStatus.OPEN) is False
    with pytest.raises(IllegalTransitionError):
        alert_at(A.ESCALATED).apply_case_status(CaseStatus.OPEN)
    with pytest.raises(InvalidValueError):
        alert_at(A.NEW).start_review(OFFICER, AT, "x" * 501)
