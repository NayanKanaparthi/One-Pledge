"""`Loan`: one pledged loan row and its state machine (SPEC 8.4).

The same transition table is enforced in the database by `onepledge_loans_guard()`
(SPEC 12.4 point 3). Terms change only while the loan is PENDING or HELD_LOCAL; the
clean stage decides whether they may change (tokens stored or not, SPEC 10.5).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime

from onepledge_bank_agent.domain.enums import LoanState
from onepledge_bank_agent.domain.errors import IllegalTransitionError, InvalidValueError
from onepledge_bank_agent.domain.values import LenderLoanNumber, LoanTerms, Money

LOAN_TRANSITIONS: frozenset[tuple[LoanState | None, LoanState]] = frozenset(
    {
        (None, LoanState.PENDING),
        (LoanState.PENDING, LoanState.HELD_LOCAL),
        (LoanState.PENDING, LoanState.LOCKED),
        (LoanState.PENDING, LoanState.CONFLICT),
        (LoanState.HELD_LOCAL, LoanState.PENDING),
        (LoanState.CONFLICT, LoanState.LOCKED),
        (LoanState.LOCKED, LoanState.RELEASED),
    }
)
_EDITABLE = frozenset({LoanState.PENDING, LoanState.HELD_LOCAL})


@dataclass(frozen=True)
class LoanRecord:
    """Everything stored about a loan row."""

    loan_id: uuid.UUID
    facility_id: uuid.UUID
    first_submission_id: uuid.UUID
    lender_loan_number: LenderLoanNumber
    terms: LoanTerms
    current_balance: Money
    state: LoanState
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class LoanDraft:
    """The facts of a loan row about to be created from an accepted tape row."""

    loan_id: uuid.UUID
    facility_id: uuid.UUID
    first_submission_id: uuid.UUID
    lender_loan_number: LenderLoanNumber
    terms: LoanTerms
    current_balance: Money


@dataclass(frozen=True)
class StateChange:
    """One loan state change, for the audit log."""

    loan_id: uuid.UUID
    from_state: LoanState | None
    to_state: LoanState


def _check_balance(balance: Money, terms: LoanTerms) -> None:
    if not 0 < balance.cents <= terms.original_amount.cents:
        raise InvalidValueError("current_balance", "OUT_OF_RANGE")


class Loan:
    """A loan row. State changes go through one method per transition of SPEC 8.4."""

    def __init__(self, record: LoanRecord) -> None:
        _check_balance(record.current_balance, record.terms)
        self._record = record

    @classmethod
    def open(cls, draft: LoanDraft, at: datetime) -> tuple[Loan, StateChange]:
        """(none) to PENDING: the clean stage creates the loan from an accepted row."""
        record = LoanRecord(
            draft.loan_id, draft.facility_id, draft.first_submission_id, draft.lender_loan_number,
            draft.terms, draft.current_balance, LoanState.PENDING, at, at,
        )  # fmt: skip
        return cls(record), StateChange(draft.loan_id, None, LoanState.PENDING)

    @property
    def record(self) -> LoanRecord:
        """The current stored form."""
        return self._record

    @property
    def loan_id(self) -> uuid.UUID:
        """The loan id."""
        return self._record.loan_id

    @property
    def state(self) -> LoanState:
        """The current state."""
        return self._record.state

    @property
    def terms(self) -> LoanTerms:
        """The canonical terms."""
        return self._record.terms

    def hold_local(self, at: datetime) -> StateChange:
        """PENDING to HELD_LOCAL: a local check failed."""
        return self._move(LoanState.PENDING, LoanState.HELD_LOCAL, at)

    def represent(self, at: datetime) -> StateChange | None:
        """HELD_LOCAL to PENDING when the lender presents the car again; no change for a PENDING loan."""
        if self._record.state is LoanState.PENDING:
            return None
        return self._move(LoanState.HELD_LOCAL, LoanState.PENDING, at)

    def lock(self, at: datetime) -> StateChange:
        """PENDING to LOCKED: registry result LOCKED."""
        return self._move(LoanState.PENDING, LoanState.LOCKED, at)

    def mark_conflict(self, at: datetime) -> StateChange:
        """PENDING to CONFLICT: registry result CONFLICT."""
        return self._move(LoanState.PENDING, LoanState.CONFLICT, at)

    def lock_after_clearance(self, at: datetime) -> StateChange:
        """CONFLICT to LOCKED: a resubmission after every case was cleared was locked."""
        return self._move(LoanState.CONFLICT, LoanState.LOCKED, at)

    def release(self, at: datetime) -> StateChange:
        """LOCKED to RELEASED: registry result RELEASED for a release item."""
        return self._move(LoanState.LOCKED, LoanState.RELEASED, at)

    def replace_terms(
        self, terms: LoanTerms, lender_loan_number: LenderLoanNumber, balance: Money, at: datetime
    ) -> None:
        """New terms, loan number and balance from a re-presented row (only PENDING or HELD_LOCAL)."""
        if self._record.state not in _EDITABLE:
            raise IllegalTransitionError("loan", self._record.state.value, "REPLACE_TERMS")
        _check_balance(balance, terms)
        self._record = replace(
            self._record,
            terms=terms,
            lender_loan_number=lender_loan_number,
            current_balance=balance,
            updated_at=at,
        )

    def update_balance(self, balance: Money, at: datetime) -> None:
        """A new current balance from a later tape; the terms stay."""
        if self._record.state is LoanState.RELEASED:
            raise IllegalTransitionError("loan", self._record.state.value, "UPDATE_BALANCE")
        _check_balance(balance, self._record.terms)
        self._record = replace(self._record, current_balance=balance, updated_at=at)

    def _move(self, expected: LoanState, target: LoanState, at: datetime) -> StateChange:
        before = self._record.state
        if before is not expected or (before, target) not in LOAN_TRANSITIONS:
            raise IllegalTransitionError("loan", before.value, target.value)
        self._record = replace(self._record, state=target, updated_at=at)
        return StateChange(self._record.loan_id, before, target)
