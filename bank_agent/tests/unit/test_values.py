"""Value objects of SPEC 8.1: invariants, masking, parsing."""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from onepledge_bank_agent.domain import (
    BorrowerRef,
    CheckResult,
    KeyVersion,
    Lei,
    LenderLoanNumber,
    LoanTerms,
    Money,
    Sha256Hex,
    Token,
    Vin,
)
from onepledge_bank_agent.domain.enums import CheckOutcome, CheckReason, CheckType, TokenKind
from onepledge_bank_agent.domain.errors import InvalidValueError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("0", 0),
        ("1", 100),
        ("23450.17", 2345017),
        ("23450.1", 2345010),
        ("0.05", 5),
        ("100000000000.00", 10**13),
    ],
)
def test_money_parses_decimal_text_exactly(text: str, cents: int) -> None:
    assert Money.parse_decimal(text).cents == cents


@pytest.mark.parametrize(
    "text", ["1.234", "-1", "1e3", "NaN", "Infinity", " 1", "1,000", "$1", "", ".5", "100000000000.01"]
)
def test_money_refuses_anything_else(text: str) -> None:
    with pytest.raises(InvalidValueError):
        Money.parse_decimal(text)


def test_money_arithmetic_comparison_and_masking() -> None:
    a, b = Money(1500), Money(250)
    assert (a + b).cents == 1750 and (a - b).cents == 1250
    assert b < a and a >= b and Money(5) == Money(5)
    assert repr(a) == "Money(***)" and "1500" not in repr(a)
    with pytest.raises(InvalidValueError):
        b - a
    for bad in (-1, 10**13 + 1):
        with pytest.raises(InvalidValueError):
            Money(bad)
    with pytest.raises(InvalidValueError):
        Money(True)


@given(st.integers(min_value=0, max_value=10**13))
def test_money_round_trips_through_its_decimal_text(cents: int) -> None:
    text = f"{cents // 100}.{cents % 100:02d}"
    assert Money.parse_decimal(text) == Money(cents)


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("C1234567", True),
        ("LK123456", True),
        ("ABCD", True),
        ("ABC", False),
        ("c1234567", False),
        ("LK-123456", False),
        ("A" * 33, False),
    ],
)
def test_borrower_reference_pattern(value: str, ok: bool) -> None:
    if ok:
        assert repr(BorrowerRef(value)) == "BorrowerRef(***)"
    else:
        with pytest.raises(InvalidValueError):
            BorrowerRef(value)


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("SAF-1234567-01", True),
        ("LMC12345678", True),
        ("a/b_c", True),
        ("-SAF", False),
        ("SAF 1", False),
        ("A" * 41, False),
        ("", False),
    ],
)
def test_lender_loan_number_pattern(value: str, ok: bool) -> None:
    if ok:
        assert repr(LenderLoanNumber(value)) == "LenderLoanNumber(***)"
    else:
        with pytest.raises(InvalidValueError):
            LenderLoanNumber(value)


@pytest.mark.parametrize("value", ["00000ONEPLEDGESUM134", "00000ONEPLEDGELAK198", "5493001KJTIIGC8Y1R12"])
def test_valid_leis(value: str) -> None:
    assert Lei(value).value == value


@pytest.mark.parametrize(
    "value", ["00000ONEPLEDGESUM135", "00000ONEPLEDGESUM1A4", "00000onepledgesum134", "00000ONEPLEDGESUM13"]
)
def test_invalid_leis(value: str) -> None:
    with pytest.raises(InvalidValueError):
        Lei(value)


def test_hashes_key_versions_and_tokens() -> None:
    digest = "a" * 64
    assert Sha256Hex(digest).value == digest
    for bad in ("A" * 64, "a" * 63, "g" * 64):
        with pytest.raises(InvalidValueError):
            Sha256Hex(bad)
    version = KeyVersion.parse("2.13")
    assert (version.holder_1, version.holder_2, str(version)) == (2, 13, "2.13")
    for bad in ("0.1", "1", "1.1.1", "a.b", "01.1"):
        with pytest.raises(InvalidValueError):
            KeyVersion.parse(bad)
    with pytest.raises(InvalidValueError):
        KeyVersion(1, 0)
    token = Token(TokenKind.ASSET, Sha256Hex(digest), version)
    assert token.kind is TokenKind.ASSET


def test_loan_terms_need_a_positive_amount_and_mask_their_values() -> None:
    vin = Vin.parse("1HGCM82633A004352")
    terms = LoanTerms(vin, BorrowerRef("C1234567"), date(2025, 1, 2), Money(100))
    assert "1HGCM" not in repr(terms) and "C1234567" not in repr(terms)
    with pytest.raises(InvalidValueError):
        LoanTerms(vin, BorrowerRef("C1234567"), date(2025, 1, 2), Money(0))


ALLOWED = {
    (CheckType.TITLE_EXISTS, CheckOutcome.PASS, CheckReason.TITLE_FOUND),
    (CheckType.TITLE_EXISTS, CheckOutcome.UNKNOWN, CheckReason.TITLE_PENDING_WINDOW),
    (CheckType.TITLE_EXISTS, CheckOutcome.FAIL, CheckReason.NO_TITLE_RECORD),
    (CheckType.LIENHOLDER_MATCH, CheckOutcome.PASS, CheckReason.LIENHOLDER_MATCHES),
    (CheckType.LIENHOLDER_MATCH, CheckOutcome.FAIL, CheckReason.LIENHOLDER_MISMATCH),
    (CheckType.LIENHOLDER_MATCH, CheckOutcome.UNKNOWN, CheckReason.TITLE_PENDING_WINDOW),
    (CheckType.LIENHOLDER_MATCH, CheckOutcome.UNKNOWN, CheckReason.NO_TITLE_RECORD),
    (CheckType.PAYMENTS_RECEIVED, CheckOutcome.UNKNOWN, CheckReason.FIRST_PAYMENT_NOT_DUE),
    (CheckType.PAYMENTS_RECEIVED, CheckOutcome.PASS, CheckReason.PAYMENT_IN_WINDOW),
    (CheckType.PAYMENTS_RECEIVED, CheckOutcome.FAIL, CheckReason.NO_PAYMENT_IN_WINDOW),
    (CheckType.CONTRACT_PRESENT, CheckOutcome.FAIL, CheckReason.NO_CONTRACT_RECORD),
    (CheckType.CONTRACT_PRESENT, CheckOutcome.FAIL, CheckReason.CONTRACT_LENDER_MISMATCH),
    (CheckType.CONTRACT_PRESENT, CheckOutcome.FAIL, CheckReason.CONTRACT_TERMS_MISMATCH),
    (CheckType.CONTRACT_PRESENT, CheckOutcome.PASS, CheckReason.CONTRACT_MATCHES),
}


def test_check_results_exist_only_in_the_combinations_of_spec_11_1() -> None:
    for check in CheckType:
        for outcome in CheckOutcome:
            for reason in CheckReason:
                if (check, outcome, reason) in ALLOWED:
                    assert CheckResult(check, outcome, reason).reason is reason
                else:
                    with pytest.raises(InvalidValueError):
                        CheckResult(check, outcome, reason)
