"""The four local checks, their boundaries, evidence and the hold decision (SPEC 11)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from onepledge_bank_agent.domain import BorrowerRef, CheckResult, LoanTerms, Money, Vin
from onepledge_bank_agent.domain.checks import CheckWindows, LocalCheckRules, holds_locally
from onepledge_bank_agent.domain.enums import CheckOutcome, CheckReason, CheckType
from onepledge_bank_agent.domain.outside_records import (
    ContractRecord,
    OutsideEvidence,
    PaymentRecord,
    PaymentWindow,
    TitleRecord,
)

pytestmark = pytest.mark.unit

AS_OF = date(2026, 10, 15)
LENDER = "Summit Auto Finance LLC"
RULES = LocalCheckRules(CheckWindows())
OUT, R = CheckOutcome, CheckReason


def terms(age_days: int, cents: int = 2345017) -> LoanTerms:
    return LoanTerms(
        Vin("1HGCM82633A004352"), BorrowerRef("C1234567"), AS_OF - timedelta(days=age_days), Money(cents)
    )


def title(lienholder: str | None = "SUMMIT AUTO FINANCE, L.L.C.") -> TitleRecord:
    return TitleRecord("NJ", "CLEAN", lienholder, date(2025, 1, 1))


def payments(*days: date) -> PaymentWindow:
    window_from, window_to = RULES.payment_window(AS_OF)
    records = tuple(PaymentRecord(uuid.uuid4(), day, 50_000, "HARBOR") for day in days)
    return PaymentWindow(window_from, window_to, records, len(records))


def contract(origination: date, cents: int = 2345017, lender: str = LENDER) -> ContractRecord:
    return ContractRecord(uuid.uuid4(), lender, cents, origination)


def evaluate(loan: LoanTerms, evidence: OutsideEvidence) -> dict[CheckType, CheckResult]:
    return {result.check_type: result for result in RULES.evaluate(loan, LENDER, AS_OF, evidence)}


def honest(age: int) -> tuple[LoanTerms, OutsideEvidence]:
    loan = terms(age)
    return loan, OutsideEvidence(
        title(), payments(AS_OF - timedelta(days=10)), (contract(loan.origination_date),)
    )


def outcome(result: CheckResult) -> tuple[CheckOutcome, CheckReason]:
    return result.outcome, result.reason


def test_an_honest_seasoned_loan_passes_all_four_in_order() -> None:
    loan, evidence = honest(200)
    results = RULES.evaluate(loan, LENDER, AS_OF, evidence)
    assert [r.check_type for r in results] == list(CheckType)
    assert [outcome(r) for r in results] == [
        (OUT.PASS, R.TITLE_FOUND),
        (OUT.PASS, R.LIENHOLDER_MATCHES),
        (OUT.PASS, R.PAYMENT_IN_WINDOW),
        (OUT.PASS, R.CONTRACT_MATCHES),
    ]
    assert not holds_locally(results)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (29, (OUT.UNKNOWN, R.TITLE_PENDING_WINDOW)),
        (30, (OUT.UNKNOWN, R.TITLE_PENDING_WINDOW)),
        (31, (OUT.FAIL, R.NO_TITLE_RECORD)),
    ],
)
def test_a_missing_title_inside_and_outside_the_pending_window(
    age: int, expected: tuple[CheckOutcome, CheckReason]
) -> None:
    loan = terms(age)
    found = evaluate(loan, OutsideEvidence(None, payments(), (contract(loan.origination_date),)))
    assert outcome(found[CheckType.TITLE_EXISTS]) == expected
    lien_reason = R.TITLE_PENDING_WINDOW if age <= 30 else R.NO_TITLE_RECORD
    assert outcome(found[CheckType.LIENHOLDER_MATCH]) == (OUT.UNKNOWN, lien_reason)
    assert found[CheckType.TITLE_EXISTS].evidence["age_days"] == age


@pytest.mark.parametrize("lienholder", [None, "Crestline Auto Credit Corp", "", "LLC"])
def test_a_title_naming_no_or_another_lienholder_fails(lienholder: str | None) -> None:
    loan = terms(200)
    found = evaluate(
        loan, OutsideEvidence(title(lienholder), payments(AS_OF), (contract(loan.origination_date),))
    )
    assert outcome(found[CheckType.LIENHOLDER_MATCH]) == (OUT.FAIL, R.LIENHOLDER_MISMATCH)


@pytest.mark.parametrize(("age", "paid", "expected"), [
    (44, (), (OUT.UNKNOWN, R.FIRST_PAYMENT_NOT_DUE)),
    (45, (), (OUT.FAIL, R.NO_PAYMENT_IN_WINDOW)),
    (45, (AS_OF,), (OUT.PASS, R.PAYMENT_IN_WINDOW)),
    (44, (AS_OF,), (OUT.UNKNOWN, R.FIRST_PAYMENT_NOT_DUE)),
])  # fmt: skip
def test_the_first_payment_grace_boundary(
    age: int, paid: tuple[date, ...], expected: tuple[CheckOutcome, CheckReason]
) -> None:
    loan = terms(age)
    found = evaluate(loan, OutsideEvidence(title(), payments(*paid), (contract(loan.origination_date),)))
    assert outcome(found[CheckType.PAYMENTS_RECEIVED]) == expected


@pytest.mark.parametrize(("offset", "counts"), [(90, True), (91, False), (0, True), (-1, False)])
def test_a_payment_on_the_first_and_last_day_of_the_window_counts(offset: int, counts: bool) -> None:
    loan = terms(200)
    found = evaluate(
        loan,
        OutsideEvidence(
            title(), payments(AS_OF - timedelta(days=offset)), (contract(loan.origination_date),)
        ),
    )
    expected = (OUT.PASS, R.PAYMENT_IN_WINDOW) if counts else (OUT.FAIL, R.NO_PAYMENT_IN_WINDOW)
    assert outcome(found[CheckType.PAYMENTS_RECEIVED]) == expected
    assert RULES.payment_window(AS_OF) == (AS_OF - timedelta(days=90), AS_OF)


def test_contract_rules() -> None:
    loan = terms(200)
    start = loan.origination_date

    def check(*found: ContractRecord) -> tuple[CheckOutcome, CheckReason]:
        return outcome(
            evaluate(loan, OutsideEvidence(title(), payments(AS_OF), found))[CheckType.CONTRACT_PRESENT]
        )

    assert check() == (OUT.FAIL, R.NO_CONTRACT_RECORD)
    assert check(contract(start, lender="Crestline Auto Credit Corp")) == (
        OUT.FAIL,
        R.CONTRACT_LENDER_MISMATCH,
    )
    assert check(contract(start, cents=2345016)) == (OUT.FAIL, R.CONTRACT_TERMS_MISMATCH)
    assert check(contract(start - timedelta(days=4))) == (OUT.FAIL, R.CONTRACT_TERMS_MISMATCH)
    assert check(contract(start + timedelta(days=4))) == (OUT.FAIL, R.CONTRACT_TERMS_MISMATCH)
    assert check(contract(start - timedelta(days=3))) == (OUT.PASS, R.CONTRACT_MATCHES)
    assert check(contract(start + timedelta(days=3))) == (OUT.PASS, R.CONTRACT_MATCHES)
    assert check(contract(start, cents=1), contract(start, lender="SUMMIT AUTO FINANCE LLC")) == (
        OUT.PASS,
        R.CONTRACT_MATCHES,
    )


def test_every_check_records_the_evidence_keys_of_spec_11_2() -> None:
    loan, evidence = honest(200)
    found = evaluate(loan, evidence)
    assert set(found[CheckType.TITLE_EXISTS].evidence) == {
        "source",
        "found",
        "title_status",
        "title_state",
        "age_days",
        "window_days",
    }
    assert set(found[CheckType.LIENHOLDER_MATCH].evidence) == {
        "source",
        "lienholder_on_title",
        "lienholder_normalised",
        "lender_normalised",
    }
    assert set(found[CheckType.PAYMENTS_RECEIVED].evidence) == {
        "source", "window_days", "window_from", "window_to", "payments_in_window", "last_paid_on",
        "deposit_bank_codes", "age_days", "grace_days",
    }  # fmt: skip
    assert set(found[CheckType.CONTRACT_PRESENT].evidence) == {
        "source", "contracts_found", "lender_matching_contracts", "matching_contract_id",
        "signed_on_tolerance_days",
    }  # fmt: skip
    payment = found[CheckType.PAYMENTS_RECEIVED].evidence
    assert payment["payments_in_window"] == 1 and payment["deposit_bank_codes"] == ["HARBOR"]
    assert payment["window_from"] == "2026-07-17" and payment["last_paid_on"] == "2026-10-05"
    assert found[CheckType.LIENHOLDER_MATCH].evidence["lender_normalised"] == "SUMMIT AUTO FINANCE"


def test_the_checks_use_the_configured_windows() -> None:
    rules = LocalCheckRules(
        CheckWindows(
            title_pending_window_days=10,
            first_payment_grace_days=5,
            payment_window_days=7,
            contract_date_tolerance_days=0,
        )
    )
    loan = terms(20)
    results = {
        r.check_type: r
        for r in rules.evaluate(
            loan,
            LENDER,
            AS_OF,
            OutsideEvidence(None, payments(), (contract(loan.origination_date + timedelta(days=1)),)),
        )
    }
    assert outcome(results[CheckType.TITLE_EXISTS]) == (OUT.FAIL, R.NO_TITLE_RECORD)
    assert outcome(results[CheckType.PAYMENTS_RECEIVED]) == (OUT.FAIL, R.NO_PAYMENT_IN_WINDOW)
    assert outcome(results[CheckType.CONTRACT_PRESENT]) == (OUT.FAIL, R.CONTRACT_TERMS_MISMATCH)
    assert rules.payment_window(AS_OF)[0] == AS_OF - timedelta(days=7)


def test_the_hold_decision_tolerates_only_the_two_waiting_reasons() -> None:
    pending = [
        CheckResult(CheckType.TITLE_EXISTS, OUT.UNKNOWN, R.TITLE_PENDING_WINDOW),
        CheckResult(CheckType.PAYMENTS_RECEIVED, OUT.UNKNOWN, R.FIRST_PAYMENT_NOT_DUE),
    ]
    assert not holds_locally(pending)
    assert holds_locally([CheckResult(CheckType.LIENHOLDER_MATCH, OUT.UNKNOWN, R.NO_TITLE_RECORD)])
    assert holds_locally([CheckResult(CheckType.CONTRACT_PRESENT, OUT.FAIL, R.NO_CONTRACT_RECORD)])
