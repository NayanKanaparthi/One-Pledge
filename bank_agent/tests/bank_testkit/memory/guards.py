"""The database's refusals, as the in-memory store applies them (SPEC_DECISIONS 92).

PostgreSQL refuses these writes in its guards (migrations 0002, 0005, 0010) and unique
keys; the in-memory repositories raise the same `StoreRefusedError` themselves, so a use
case that writes something the database would refuse fails its unit test too. Only what
a repository port can write is checked here: a tape's progress, a loan's mutable
columns, an alert's review and statuses, and the first state of each.
"""

from __future__ import annotations

from onepledge_bank_agent.domain.alert import Alert
from onepledge_bank_agent.domain.enums import AlertStatus, CaseStatus, LoanState, TapeStatus
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.store_errors import StoreRefusedError
from onepledge_bank_agent.domain.tape import NEXT_STAGE, TapeProgress, TapeSubmission

LOAN_MOVES = frozenset({
    (LoanState.PENDING, LoanState.HELD_LOCAL), (LoanState.PENDING, LoanState.LOCKED),
    (LoanState.PENDING, LoanState.CONFLICT), (LoanState.HELD_LOCAL, LoanState.PENDING),
    (LoanState.CONFLICT, LoanState.LOCKED), (LoanState.LOCKED, LoanState.RELEASED),
})  # fmt: skip
ALERT_MOVES = frozenset({
    (AlertStatus.NEW, AlertStatus.REVIEWING), (AlertStatus.NEW, AlertStatus.CLEARED),
    (AlertStatus.NEW, AlertStatus.ESCALATED), (AlertStatus.REVIEWING, AlertStatus.CLEARED),
    (AlertStatus.REVIEWING, AlertStatus.ESCALATED),
})  # fmt: skip
CLOSED = frozenset({AlertStatus.CLEARED, AlertStatus.ESCALATED})
CASE_OF = {
    AlertStatus.NEW: frozenset({CaseStatus.OPEN}),
    AlertStatus.REVIEWING: frozenset({CaseStatus.OPEN}),
    AlertStatus.CLEARED: frozenset({CaseStatus.CLEARED_DATA_ERROR, CaseStatus.CLEARED_LATE_RELEASE}),
    AlertStatus.ESCALATED: frozenset({CaseStatus.CONFIRMED}),
}


def refuse(code: str) -> StoreRefusedError:
    """The refusal a guard with this SQLSTATE raises."""
    return StoreRefusedError(code, code)


def _no_failure(progress: TapeProgress) -> bool:
    return progress.resume_status is None and progress.failed_stage is None and progress.failure_code is None


def check_tape_insert(tape: TapeSubmission) -> None:
    """A submission is created RECEIVED, with no failure, completion or retry facts (OP004)."""
    p = tape.progress
    fresh = p.status is TapeStatus.RECEIVED and _no_failure(p) and p.completed_at is None
    if not (fresh and p.next_attempt_at is None and p.attempts == 0):
        raise refuse("OP004")


def check_tape_save(old: TapeProgress, new: TapeProgress) -> None:
    """The tape guard of migrations 0005 and 0010 (OP004)."""
    if old.status is TapeStatus.COMPLETED and new != old:
        raise refuse("OP004")
    following = NEXT_STAGE.get(old.status)
    if old.status is TapeStatus.FAILED and new.status is TapeStatus.FAILED:
        kept = (old.resume_status, old.failed_stage, old.failure_code, old.attempts, old.next_attempt_at)
        allowed = kept == (
            new.resume_status,
            new.failed_stage,
            new.failure_code,
            new.attempts,
            new.next_attempt_at,
        )
    elif old.status is TapeStatus.FAILED:
        allowed = new.status is old.resume_status and _no_failure(new)
    elif new.status is TapeStatus.FAILED:
        allowed = (
            following is not None
            and new.resume_status is old.status
            and new.failed_stage is following
            and new.failure_code is not None
        )
    elif new.status is old.status:
        allowed = _no_failure(new)
    else:
        allowed = new.status is following and _no_failure(new)
    if not allowed:
        raise refuse("OP004")


def check_loan_insert(loan: Loan) -> None:
    """A loan is created PENDING (OP002)."""
    if loan.state is not LoanState.PENDING:
        raise refuse("OP002")


def check_loan_save(old: Loan, new: Loan, tokenised: bool) -> None:
    """The loans guard of migration 0010, on the columns the repository writes."""
    before, after = old.record, new.record
    if before.state is LoanState.RELEASED and after != before:
        raise refuse("OP002")
    if after.terms.vin != before.terms.vin:
        raise refuse("OP003")
    if after.state is not before.state and (before.state, after.state) not in LOAN_MOVES:
        raise refuse("OP002")
    terms_changed = (
        after.terms.borrower_ref != before.terms.borrower_ref
        or after.terms.origination_date != before.terms.origination_date
        or after.terms.original_amount != before.terms.original_amount
        or after.lender_loan_number != before.lender_loan_number
    )
    if terms_changed and tokenised:
        raise refuse("OP003")


def check_alert_insert(alert: Alert) -> None:
    """An alert is created NEW with its case OPEN and no review or position (OP006)."""
    record = alert.record
    review = record.review
    unreviewed = review.reviewed_by is None and review.reviewed_at is None
    unpositioned = review.bank_position is None and review.position_submitted_at is None
    if not (
        record.status is AlertStatus.NEW
        and record.case_status is CaseStatus.OPEN
        and unreviewed
        and unpositioned
    ):
        raise refuse("OP006")


def check_alert_save(old: Alert, new: Alert) -> None:
    """The alerts guard and the pair constraint of migration 0010, on the columns the repository writes."""
    before, after = old.record, new.record
    if before.status in CLOSED and after != before:
        raise refuse("OP006")
    if after.status is not before.status and (before.status, after.status) not in ALERT_MOVES:
        raise refuse("OP006")
    if after.case_status not in CASE_OF[after.status]:
        raise refuse("OP006")
    reviewing = before.status is AlertStatus.REVIEWING
    position = (before.review.bank_position, after.review.bank_position)
    if position[0] != position[1] and (position[0] is not None or not reviewing):
        raise refuse("OP006")
    submitted = (before.review.position_submitted_at, after.review.position_submitted_at)
    if submitted[0] != submitted[1] and (submitted[0] is not None or not reviewing):
        raise refuse("OP006")
    if after.case_status is not before.case_status and before.case_status is not CaseStatus.OPEN:
        raise refuse("OP006")
