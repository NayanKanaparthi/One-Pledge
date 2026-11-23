"""Value objects of the bank agent (SPEC 8.1). Immutable and validated on construction.

Values that are raw loan data (money, borrower reference, loan number) have a masked
`repr`, so printing a loan or an error never shows them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Self

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import (
    CheckOutcome,
    CheckReason,
    CheckType,
    CleaningAction,
    CleaningProblem,
    TokenKind,
)
from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.storable import require_document_size
from onepledge_bank_agent.domain.text_rules import DIGITS, parse_integer
from onepledge_bank_agent.domain.vin import Vin

MAX_CENTS = 10**13
_DECIMAL_AMOUNT = re.compile(r"^([0-9]{1,13})(?:\.([0-9]{1,2}))?$")
_BORROWER_REF = re.compile(r"^[A-Z0-9]{4,32}$")
_LOAN_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_/]{0,39}$")
_LEI = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY_VERSION = re.compile(r"^([1-9][0-9]*)\.([1-9][0-9]*)$")
_LEI_MODULUS = 97
MASKED = "***"


@dataclass(frozen=True, order=True, repr=False)
class Money:
    """An amount in integer cents, `0 <= cents <= 10**13`. No float is ever involved."""

    cents: int

    def __post_init__(self) -> None:
        """Refuse a non integer or an amount out of range."""
        if isinstance(self.cents, bool) or not isinstance(self.cents, int):
            raise InvalidValueError("money", "INVALID_TYPE")
        if not 0 <= self.cents <= MAX_CENTS:
            raise InvalidValueError("money", "OUT_OF_RANGE")

    @classmethod
    def parse_decimal(cls, text: str) -> Self:
        """Parse `123` or `123.4` or `123.45` in ASCII digits; at most two fraction digits; exact cents."""
        match = _DECIMAL_AMOUNT.fullmatch(text)
        if match is None:
            raise InvalidValueError("money", "PATTERN_MISMATCH")
        dollars = parse_integer(match[1], "money", 0)
        fraction = (match[2] or "").ljust(2, "0")
        return cls(dollars * 100 + parse_integer(fraction, "money", 0))

    def __add__(self, other: Money) -> Money:
        """Sum of two amounts."""
        return Money(self.cents + other.cents)

    def __sub__(self, other: Money) -> Money:
        """Difference of two amounts; refuses a negative result."""
        return Money(self.cents - other.cents)

    def __repr__(self) -> str:
        """Masked."""
        return f"Money({MASKED})"


@dataclass(frozen=True, repr=False)
class BorrowerRef:
    """A lender's own customer number in canonical form, `^[A-Z0-9]{4,32}$`."""

    value: str

    def __post_init__(self) -> None:
        """Refuse a non canonical reference."""
        if not _BORROWER_REF.fullmatch(self.value):
            raise InvalidValueError("borrower_ref", "PATTERN_MISMATCH")

    def __repr__(self) -> str:
        """Masked."""
        return f"BorrowerRef({MASKED})"


@dataclass(frozen=True, repr=False)
class LenderLoanNumber:
    """The lender's loan number, `^[A-Za-z0-9][A-Za-z0-9\\-_/]{0,39}$`. Displayed, never tokenised."""

    value: str

    def __post_init__(self) -> None:
        """Refuse anything outside the pattern."""
        if not _LOAN_NUMBER.fullmatch(self.value):
            raise InvalidValueError("lender_loan_number", "PATTERN_MISMATCH")

    def __repr__(self) -> str:
        """Masked."""
        return f"LenderLoanNumber({MASKED})"


def lei_checksum(text: str) -> int:
    """ISO 17442 / ISO 7064 MOD 97-10 remainder; letters count A=10 to Z=35."""
    remainder = 0
    for char in text:
        value = ord(char) - ord("0") if char in DIGITS else ord(char) - ord("A") + 10
        for digit in str(value):
            remainder = (remainder * 10 + ord(digit) - ord("0")) % _LEI_MODULUS
    return remainder


@dataclass(frozen=True)
class Lei:
    """A legal entity identifier: 18 alphanumerics, 2 check digits, MOD 97 remainder 1."""

    value: str

    def __post_init__(self) -> None:
        """Refuse a bad shape or bad check digits."""
        if not _LEI.fullmatch(self.value):
            raise InvalidValueError("lei", "PATTERN_MISMATCH")
        if lei_checksum(self.value) != 1:
            raise InvalidValueError("lei", "INVALID_VALUE")


@dataclass(frozen=True)
class Sha256Hex:
    """A SHA-256 digest as 64 lower case hex characters."""

    value: str

    def __post_init__(self) -> None:
        """Refuse anything else."""
        if not _SHA256.fullmatch(self.value):
            raise InvalidValueError("sha256", "PATTERN_MISMATCH")


@dataclass(frozen=True, order=True)
class KeyVersion:
    """The pair of key holder versions a token was made with; string form `"<h1>.<h2>"`."""

    holder_1: int
    holder_2: int

    def __post_init__(self) -> None:
        """Both versions are positive integers."""
        for value in (self.holder_1, self.holder_2):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise InvalidValueError("key_version", "OUT_OF_RANGE")

    @classmethod
    def parse(cls, text: str) -> Self:
        """Parse `"1.1"`."""
        match = _KEY_VERSION.fullmatch(text)
        if match is None:
            raise InvalidValueError("key_version", "PATTERN_MISMATCH")
        return cls(parse_integer(match[1], "key_version", 1), parse_integer(match[2], "key_version", 1))

    def __str__(self) -> str:
        """`"<h1>.<h2>"`."""
        return f"{self.holder_1}.{self.holder_2}"


@dataclass(frozen=True)
class Token:
    """One scrambled code of a loan: kind, 64 hex characters, and the key version it was made with."""

    kind: TokenKind
    value: Sha256Hex
    key_version: KeyVersion


@dataclass(frozen=True)
class LoanTerms:
    """The four canonical terms of a loan. They are the token inputs and never change once tokenised."""

    vin: Vin
    borrower_ref: BorrowerRef
    origination_date: date
    original_amount: Money

    def __post_init__(self) -> None:
        """A loan has a positive original amount."""
        if self.original_amount.cents <= 0:
            raise InvalidValueError("original_amount", "OUT_OF_RANGE")


ALLOWED_CHECK_RESULTS: Mapping[CheckType, frozenset[tuple[CheckOutcome, CheckReason]]] = MappingProxyType(
    {
        CheckType.TITLE_EXISTS: frozenset(
            {
                (CheckOutcome.PASS, CheckReason.TITLE_FOUND),
                (CheckOutcome.UNKNOWN, CheckReason.TITLE_PENDING_WINDOW),
                (CheckOutcome.FAIL, CheckReason.NO_TITLE_RECORD),
            }
        ),
        CheckType.LIENHOLDER_MATCH: frozenset(
            {
                (CheckOutcome.PASS, CheckReason.LIENHOLDER_MATCHES),
                (CheckOutcome.FAIL, CheckReason.LIENHOLDER_MISMATCH),
                (CheckOutcome.UNKNOWN, CheckReason.TITLE_PENDING_WINDOW),
                (CheckOutcome.UNKNOWN, CheckReason.NO_TITLE_RECORD),
            }
        ),
        CheckType.PAYMENTS_RECEIVED: frozenset(
            {
                (CheckOutcome.UNKNOWN, CheckReason.FIRST_PAYMENT_NOT_DUE),
                (CheckOutcome.PASS, CheckReason.PAYMENT_IN_WINDOW),
                (CheckOutcome.FAIL, CheckReason.NO_PAYMENT_IN_WINDOW),
            }
        ),
        CheckType.CONTRACT_PRESENT: frozenset(
            {
                (CheckOutcome.FAIL, CheckReason.NO_CONTRACT_RECORD),
                (CheckOutcome.FAIL, CheckReason.CONTRACT_LENDER_MISMATCH),
                (CheckOutcome.FAIL, CheckReason.CONTRACT_TERMS_MISMATCH),
                (CheckOutcome.PASS, CheckReason.CONTRACT_MATCHES),
            }
        ),
    }
)


@dataclass(frozen=True)
class CheckResult:
    """One local check result with the rule it rests on and its evidence (SPEC 11.1, 11.2)."""

    check_type: CheckType
    outcome: CheckOutcome
    reason: CheckReason
    evidence: Mapping[str, JsonValue] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        """Only the combinations of SPEC 11.1 exist; the evidence is a free-form document of at most
        64 KiB (SPEC_DECISIONS 125)."""
        if (self.outcome, self.reason) not in ALLOWED_CHECK_RESULTS[self.check_type]:
            raise InvalidValueError("check_result", "INVALID_VALUE")
        require_document_size("evidence", self.evidence)


@dataclass(frozen=True, order=True)
class CleaningIssue:
    """One problem cleaning found in one field (or `row`) and what it did about it."""

    field: str
    problem: CleaningProblem
    action: CleaningAction
