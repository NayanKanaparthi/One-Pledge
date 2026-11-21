"""Result types of cleaning: per field results and the outcome of one row."""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_bank_agent.domain.enums import CleaningAction, CleaningProblem
from onepledge_bank_agent.domain.values import CleaningIssue, LenderLoanNumber, LoanTerms, Money
from onepledge_bank_agent.domain.vin import Vin


def fixed(field: str, problem: CleaningProblem) -> CleaningIssue:
    """A FIXED issue."""
    return CleaningIssue(field, problem, CleaningAction.FIXED)


def rejected(field: str, problem: CleaningProblem) -> CleaningIssue:
    """A REJECTED issue."""
    return CleaningIssue(field, problem, CleaningAction.REJECTED)


@dataclass(frozen=True)
class FieldResult[T]:
    """The canonical value of one field (None when rejected) and the issues found on the way."""

    value: T | None
    issues: tuple[CleaningIssue, ...] = ()

    @classmethod
    def refused(cls, field: str, problem: CleaningProblem, *earlier: CleaningIssue) -> FieldResult[T]:
        """Rejected, keeping the FIXED issues recorded before the rejection."""
        return cls(None, (*earlier, rejected(field, problem)))


@dataclass(frozen=True)
class LoanValues:
    """The canonical values of an accepted BACK_BOOK or PLEDGE row."""

    lender_loan_number: LenderLoanNumber
    terms: LoanTerms
    current_balance: Money


@dataclass(frozen=True)
class CleanedRow:
    """A row with no REJECTED issue. `loan` is None for a RELEASE row (only the VIN is read)."""

    issues: tuple[CleaningIssue, ...]
    vin: Vin
    loan: LoanValues | None


@dataclass(frozen=True)
class RejectedRow:
    """A row with at least one REJECTED issue; every issue found is kept for the review list."""

    issues: tuple[CleaningIssue, ...]
