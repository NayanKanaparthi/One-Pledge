"""Vocabulary of the oracle: the codes of SPEC sections 10, 11 and 13, and per row observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto


class Action(StrEnum):
    """What cleaning did about a problem."""

    FIXED = "FIXED"
    REJECTED = "REJECTED"


class Problem(StrEnum):
    """Cleaning problems of SPEC 10.4 and 10.5."""

    WHITESPACE_TRIMMED = "WHITESPACE_TRIMMED"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    NON_DATA_ROW = "NON_DATA_ROW"
    COLUMN_COUNT_MISMATCH = "COLUMN_COUNT_MISMATCH"
    UNSAFE_CHARACTERS = "UNSAFE_CHARACTERS"
    VIN_CASE_NORMALISED = "VIN_CASE_NORMALISED"
    VIN_SEPARATORS_REMOVED = "VIN_SEPARATORS_REMOVED"
    VIN_CHARACTER_SUBSTITUTED = "VIN_CHARACTER_SUBSTITUTED"
    VIN_LENGTH_INVALID = "VIN_LENGTH_INVALID"
    VIN_CHARACTER_INVALID = "VIN_CHARACTER_INVALID"
    VIN_CHECK_DIGIT_INVALID = "VIN_CHECK_DIGIT_INVALID"
    BORROWER_REF_NORMALISED = "BORROWER_REF_NORMALISED"
    BORROWER_REF_INVALID = "BORROWER_REF_INVALID"
    LOAN_NUMBER_INVALID = "LOAN_NUMBER_INVALID"
    DATE_FORMAT_NORMALISED = "DATE_FORMAT_NORMALISED"
    DATE_UNPARSEABLE = "DATE_UNPARSEABLE"
    DATE_IN_FUTURE = "DATE_IN_FUTURE"
    DATE_TOO_OLD = "DATE_TOO_OLD"
    AMOUNT_FORMAT_NORMALISED = "AMOUNT_FORMAT_NORMALISED"
    AMOUNT_UNPARSEABLE = "AMOUNT_UNPARSEABLE"
    AMOUNT_OUT_OF_RANGE = "AMOUNT_OUT_OF_RANGE"
    ZERO_BALANCE = "ZERO_BALANCE"
    BALANCE_EXCEEDS_ORIGINAL = "BALANCE_EXCEEDS_ORIGINAL"
    DUPLICATE_VIN_IN_FILE = "DUPLICATE_VIN_IN_FILE"
    TERMS_CHANGED_AFTER_TOKENIZATION = "TERMS_CHANGED_AFTER_TOKENIZATION"
    TERMS_CHANGED_WHILE_LOCKED = "TERMS_CHANGED_WHILE_LOCKED"
    OPEN_CONFLICT = "OPEN_CONFLICT"
    RELEASE_LOAN_NOT_LOCKED = "RELEASE_LOAN_NOT_LOCKED"


class Disposition(StrEnum):
    """Per row disposition of SPEC 10.5."""

    ACCEPTED_NEW = "ACCEPTED_NEW"
    ACCEPTED_REPRESENTED = "ACCEPTED_REPRESENTED"
    ALREADY_LOCKED = "ALREADY_LOCKED"
    RELEASE_REQUESTED = "RELEASE_REQUESTED"
    REJECTED = "REJECTED"


class CheckType(StrEnum):
    """The four local checks, in their fixed order."""

    TITLE_EXISTS = "TITLE_EXISTS"
    LIENHOLDER_MATCH = "LIENHOLDER_MATCH"
    PAYMENTS_RECEIVED = "PAYMENTS_RECEIVED"
    CONTRACT_PRESENT = "CONTRACT_PRESENT"


class _NamedStrEnum(StrEnum):
    """A StrEnum whose `auto()` values equal the member names."""

    @staticmethod
    def _generate_next_value_(name: str, start: int, count: int, last_values: list[str]) -> str:
        """Each member's value is its own name."""
        return name


class CheckOutcome(_NamedStrEnum):
    """Result of one local check."""

    PASS = auto()
    FAIL = auto()
    UNKNOWN = auto()


class Reason(StrEnum):
    """Reason codes of SPEC 11.1."""

    TITLE_FOUND = "TITLE_FOUND"
    TITLE_PENDING_WINDOW = "TITLE_PENDING_WINDOW"
    NO_TITLE_RECORD = "NO_TITLE_RECORD"
    LIENHOLDER_MATCHES = "LIENHOLDER_MATCHES"
    LIENHOLDER_MISMATCH = "LIENHOLDER_MISMATCH"
    FIRST_PAYMENT_NOT_DUE = "FIRST_PAYMENT_NOT_DUE"
    PAYMENT_IN_WINDOW = "PAYMENT_IN_WINDOW"
    NO_PAYMENT_IN_WINDOW = "NO_PAYMENT_IN_WINDOW"
    NO_CONTRACT_RECORD = "NO_CONTRACT_RECORD"
    CONTRACT_LENDER_MISMATCH = "CONTRACT_LENDER_MISMATCH"
    CONTRACT_TERMS_MISMATCH = "CONTRACT_TERMS_MISMATCH"
    CONTRACT_MATCHES = "CONTRACT_MATCHES"


class LoanState(StrEnum):
    """Bank side loan states of SPEC 8.4."""

    PENDING = "PENDING"
    HELD_LOCAL = "HELD_LOCAL"
    LOCKED = "LOCKED"
    CONFLICT = "CONFLICT"
    RELEASED = "RELEASED"


class RowOutcome(StrEnum):
    """The scorecard's row outcome of SPEC 20.4."""

    REJECTED = "REJECTED"
    HELD_LOCAL = "HELD_LOCAL"
    LOCKED = "LOCKED"
    CONFLICT = "CONFLICT"
    RELEASED = "RELEASED"
    OTHER = "OTHER"


FIELD_ORDER: tuple[str, ...] = (
    "row",
    "lender_loan_number",
    "vin",
    "borrower_ref",
    "origination_date",
    "original_amount",
    "current_balance",
)


@dataclass(frozen=True, order=True)
class Issue:
    """One cleaning issue: field, problem and action."""

    field: str
    problem: Problem
    action: Action

    def sort_key(self) -> tuple[int, str]:
        """Field order of the review list, then problem code."""
        return (FIELD_ORDER.index(self.field), self.problem.value)


@dataclass(frozen=True)
class Terms:
    """Canonical loan terms of an accepted row (the token inputs) plus loan number and balance."""

    vin: str
    borrower_ref: str
    origination_date: str
    original_amount_cents: int
    lender_loan_number: str
    current_balance_cents: int

    def same_terms(self, other: Terms) -> bool:
        """VIN, borrower, origination date and original amount are equal."""
        return (self.vin, self.borrower_ref, self.origination_date, self.original_amount_cents) == (
            other.vin,
            other.borrower_ref,
            other.origination_date,
            other.original_amount_cents,
        )


@dataclass(frozen=True)
class CleanedRow:
    """The result of cleaning one row: issues, and canonical values when nothing was rejected."""

    issues: tuple[Issue, ...]
    vin: str | None = None
    terms: Terms | None = None

    @property
    def rejected(self) -> bool:
        """At least one issue was REJECTED."""
        return any(issue.action == Action.REJECTED for issue in self.issues)


@dataclass(frozen=True)
class CheckResult:
    """One local check result with its reason."""

    check_type: CheckType
    outcome: CheckOutcome
    reason: Reason


@dataclass
class RowObservation:
    """What the oracle predicts for one tape row."""

    step_id: str
    row_number: int
    disposition: Disposition
    issues: tuple[Issue, ...]
    checks: tuple[CheckResult, ...] = ()
    outcome: RowOutcome = RowOutcome.OTHER
    match_kind: str | None = None
    match_basis: str | None = None
    kinds_matched: tuple[str, ...] = ()
    loan_ref: tuple[str, int] | None = None
    local_state: LoanState | None = None
    alerts: list[tuple[str, str]] = field(default_factory=list)
