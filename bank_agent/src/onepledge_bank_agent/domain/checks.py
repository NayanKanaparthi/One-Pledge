"""The four local checks and the hold decision (SPEC 11.1 to 11.3), as pure rules with evidence.

All windows are measured from the tape's as-of date, never from the wall clock. "Age"
is the as-of date minus the origination date, in days. All four checks always run.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import CheckOutcome, CheckReason, CheckType
from onepledge_bank_agent.domain.lender_names import lender_names_match, normalise_lender_name
from onepledge_bank_agent.domain.outside_records import (
    ContractRecord,
    OutsideEvidence,
    PaymentWindow,
    TitleRecord,
)
from onepledge_bank_agent.domain.values import CheckResult, LoanTerms

TITLE_SOURCE = "outside_records GET /v1/titles/{vin}"
PAYMENTS_SOURCE = "outside_records GET /v1/payments"
CONTRACTS_SOURCE = "outside_records GET /v1/contracts/{vin}"
TOLERATED_UNKNOWN = frozenset({CheckReason.TITLE_PENDING_WINDOW, CheckReason.FIRST_PAYMENT_NOT_DUE})


@dataclass(frozen=True)
class CheckWindows:
    """The tunables of SPEC 3.3 the checks depend on."""

    title_pending_window_days: int = 30
    first_payment_grace_days: int = 45
    payment_window_days: int = 90
    contract_date_tolerance_days: int = 3


def _result(
    check: CheckType, outcome: CheckOutcome, reason: CheckReason, evidence: Mapping[str, JsonValue]
) -> CheckResult:
    return CheckResult(check, outcome, reason, dict(evidence))


class LocalCheckRules:
    """Evaluates the four checks of one loan from the outside evidence."""

    def __init__(self, windows: CheckWindows) -> None:
        self._windows = windows

    def payment_window(self, as_of_date: date) -> tuple[date, date]:
        """Inclusive window of the payments call: as-of minus PAYMENT_WINDOW_DAYS to as-of."""
        return as_of_date - timedelta(days=self._windows.payment_window_days), as_of_date

    def evaluate(
        self, terms: LoanTerms, lender_legal_name: str, as_of_date: date, evidence: OutsideEvidence
    ) -> tuple[CheckResult, CheckResult, CheckResult, CheckResult]:
        """The four results in the fixed check order."""
        age = (as_of_date - terms.origination_date).days
        title = self._title_exists(evidence.title, age)
        return (
            title,
            self._lienholder(evidence.title, title, lender_legal_name),
            self._payments(evidence.payments, age),
            self._contract(evidence.contracts, terms, lender_legal_name),
        )

    def _title_exists(self, title: TitleRecord | None, age: int) -> CheckResult:
        window = self._windows.title_pending_window_days
        evidence: dict[str, JsonValue] = {
            "source": TITLE_SOURCE,
            "found": title is not None,
            "title_status": title.title_status if title else None,
            "title_state": title.state if title else None,
            "age_days": age,
            "window_days": window,
        }
        if title is not None:
            return _result(CheckType.TITLE_EXISTS, CheckOutcome.PASS, CheckReason.TITLE_FOUND, evidence)
        if age <= window:
            return _result(
                CheckType.TITLE_EXISTS, CheckOutcome.UNKNOWN, CheckReason.TITLE_PENDING_WINDOW, evidence
            )
        return _result(CheckType.TITLE_EXISTS, CheckOutcome.FAIL, CheckReason.NO_TITLE_RECORD, evidence)

    @staticmethod
    def _lienholder(title: TitleRecord | None, title_result: CheckResult, lender: str) -> CheckResult:
        lienholder = title.lienholder_name if title else None
        evidence: dict[str, JsonValue] = {
            "source": TITLE_SOURCE,
            "lienholder_on_title": lienholder,
            "lienholder_normalised": normalise_lender_name(lienholder) if title else None,
            "lender_normalised": normalise_lender_name(lender),
        }
        check = CheckType.LIENHOLDER_MATCH
        if title is None:
            return _result(check, CheckOutcome.UNKNOWN, title_result.reason, evidence)
        if lender_names_match(lienholder, lender):
            return _result(check, CheckOutcome.PASS, CheckReason.LIENHOLDER_MATCHES, evidence)
        return _result(check, CheckOutcome.FAIL, CheckReason.LIENHOLDER_MISMATCH, evidence)

    def _payments(self, window: PaymentWindow, age: int) -> CheckResult:
        in_window = [p for p in window.payments if window.window_from <= p.paid_on <= window.window_to]
        last = max((p.paid_on for p in in_window), default=None)
        evidence: dict[str, JsonValue] = {
            "source": PAYMENTS_SOURCE,
            "window_days": self._windows.payment_window_days,
            "window_from": window.window_from.isoformat(),
            "window_to": window.window_to.isoformat(),
            "payments_in_window": len(in_window),
            "last_paid_on": last.isoformat() if last else None,
            "deposit_bank_codes": sorted({p.deposit_bank_code for p in in_window}),
            "age_days": age,
            "grace_days": self._windows.first_payment_grace_days,
        }
        check = CheckType.PAYMENTS_RECEIVED
        if age < self._windows.first_payment_grace_days:
            return _result(check, CheckOutcome.UNKNOWN, CheckReason.FIRST_PAYMENT_NOT_DUE, evidence)
        if in_window:
            return _result(check, CheckOutcome.PASS, CheckReason.PAYMENT_IN_WINDOW, evidence)
        return _result(check, CheckOutcome.FAIL, CheckReason.NO_PAYMENT_IN_WINDOW, evidence)

    def _contract(self, contracts: Iterable[ContractRecord], terms: LoanTerms, lender: str) -> CheckResult:
        found = tuple(contracts)
        tolerance = self._windows.contract_date_tolerance_days
        matching_lender = [c for c in found if lender_names_match(c.lender_name, lender)]
        matching = next(
            (
                c
                for c in matching_lender
                if c.amount_cents == terms.original_amount.cents
                and abs((c.signed_on - terms.origination_date).days) <= tolerance
            ),
            None,
        )
        evidence: dict[str, JsonValue] = {
            "source": CONTRACTS_SOURCE,
            "contracts_found": len(found),
            "lender_matching_contracts": len(matching_lender),
            "matching_contract_id": str(matching.contract_id) if matching else None,
            "signed_on_tolerance_days": tolerance,
        }
        check = CheckType.CONTRACT_PRESENT
        if not found:
            return _result(check, CheckOutcome.FAIL, CheckReason.NO_CONTRACT_RECORD, evidence)
        if not matching_lender:
            return _result(check, CheckOutcome.FAIL, CheckReason.CONTRACT_LENDER_MISMATCH, evidence)
        if matching is None:
            return _result(check, CheckOutcome.FAIL, CheckReason.CONTRACT_TERMS_MISMATCH, evidence)
        return _result(check, CheckOutcome.PASS, CheckReason.CONTRACT_MATCHES, evidence)


def holds_locally(results: Iterable[CheckResult]) -> bool:
    """Any FAIL holds the loan; an UNKNOWN is tolerated only for the two waiting reasons (SPEC 11.3)."""
    return any(
        result.outcome is CheckOutcome.FAIL
        or (result.outcome is CheckOutcome.UNKNOWN and result.reason not in TOLERATED_UNKNOWN)
        for result in results
    )
