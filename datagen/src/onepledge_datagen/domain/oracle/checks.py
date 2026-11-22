"""The four local checks of SPEC 11 (oracle implementation).

Windows are measured from the tape's as-of date with the default tunables of
SPEC 3.3: title pending window 30 days, first payment grace 45 days, payment
window 90 days, contract date tolerance 3 days.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from onepledge_datagen.domain.entities import ContractRecord, PaymentRecord, TitleRecord
from onepledge_datagen.domain.oracle.lender_names import names_match
from onepledge_datagen.domain.oracle.model import CheckOutcome, CheckResult, CheckType, Reason, Terms
from onepledge_datagen.domain.text_values import parse_iso_date


def canonical_date(text: str) -> date:
    """The day of a canonical `YYYY-MM-DD` date the cleaning rules produced."""
    day = parse_iso_date(text)
    if day is None:
        raise ValueError(text)
    return day


@dataclass(frozen=True)
class CheckSettings:
    """The tunables the checks depend on."""

    title_pending_window_days: int = 30
    first_payment_grace_days: int = 45
    payment_window_days: int = 90
    contract_date_tolerance_days: int = 3


class OutsideRecords(Protocol):
    """Read access to the outside records of one data set."""

    def title(self, vin: str) -> TitleRecord | None:
        """The title record of a VIN, if any."""
        ...

    def payments(self, vin: str, first: date, last: date) -> Sequence[PaymentRecord]:
        """Payments of a VIN with `first <= paid_on <= last`."""
        ...

    def contracts(self, vin: str) -> Sequence[ContractRecord]:
        """Contract records of a VIN."""
        ...


class LocalChecks:
    """Runs all four checks for one accepted loan, with no short circuit."""

    def __init__(self, records: OutsideRecords, settings: CheckSettings | None = None) -> None:
        self._records = records
        self._settings = settings or CheckSettings()

    def run(self, terms: Terms, lender_legal_name: str, as_of: date) -> tuple[CheckResult, ...]:
        """The four results in the fixed check order."""
        settings = self._settings
        origination = canonical_date(terms.origination_date)
        age = (as_of - origination).days
        title = self._records.title(terms.vin)
        if title is not None:
            title_result = CheckResult(CheckType.TITLE_EXISTS, CheckOutcome.PASS, Reason.TITLE_FOUND)
            lien = (
                CheckResult(CheckType.LIENHOLDER_MATCH, CheckOutcome.PASS, Reason.LIENHOLDER_MATCHES)
                if names_match(title.lienholder_name, lender_legal_name)
                else CheckResult(CheckType.LIENHOLDER_MATCH, CheckOutcome.FAIL, Reason.LIENHOLDER_MISMATCH)
            )
        elif age <= settings.title_pending_window_days:
            title_result = CheckResult(
                CheckType.TITLE_EXISTS, CheckOutcome.UNKNOWN, Reason.TITLE_PENDING_WINDOW
            )
            lien = CheckResult(CheckType.LIENHOLDER_MATCH, CheckOutcome.UNKNOWN, Reason.TITLE_PENDING_WINDOW)
        else:
            title_result = CheckResult(CheckType.TITLE_EXISTS, CheckOutcome.FAIL, Reason.NO_TITLE_RECORD)
            lien = CheckResult(CheckType.LIENHOLDER_MATCH, CheckOutcome.UNKNOWN, Reason.NO_TITLE_RECORD)
        return (
            title_result,
            lien,
            self._payments(terms.vin, age, as_of),
            self._contract(terms, lender_legal_name),
        )

    def _payments(self, vin: str, age: int, as_of: date) -> CheckResult:
        """PAYMENTS_RECEIVED: UNKNOWN before the first payment is due, else a payment in the window."""
        window_start = as_of - timedelta(days=self._settings.payment_window_days)
        found = self._records.payments(vin, window_start, as_of)
        if age < self._settings.first_payment_grace_days:
            return CheckResult(
                CheckType.PAYMENTS_RECEIVED, CheckOutcome.UNKNOWN, Reason.FIRST_PAYMENT_NOT_DUE
            )
        if found:
            return CheckResult(CheckType.PAYMENTS_RECEIVED, CheckOutcome.PASS, Reason.PAYMENT_IN_WINDOW)
        return CheckResult(CheckType.PAYMENTS_RECEIVED, CheckOutcome.FAIL, Reason.NO_PAYMENT_IN_WINDOW)

    def _contract(self, terms: Terms, lender_legal_name: str) -> CheckResult:
        """CONTRACT_PRESENT: a contract of the facility lender with the amount and a near signing date."""
        contracts = self._records.contracts(terms.vin)
        if not contracts:
            return CheckResult(CheckType.CONTRACT_PRESENT, CheckOutcome.FAIL, Reason.NO_CONTRACT_RECORD)
        lender_matching = [c for c in contracts if names_match(c.lender_name, lender_legal_name)]
        if not lender_matching:
            return CheckResult(CheckType.CONTRACT_PRESENT, CheckOutcome.FAIL, Reason.CONTRACT_LENDER_MISMATCH)
        origination = canonical_date(terms.origination_date)
        tolerance = self._settings.contract_date_tolerance_days
        if any(
            c.amount_cents == terms.original_amount_cents
            and abs((c.signed_on - origination).days) <= tolerance
            for c in lender_matching
        ):
            return CheckResult(CheckType.CONTRACT_PRESENT, CheckOutcome.PASS, Reason.CONTRACT_MATCHES)
        return CheckResult(CheckType.CONTRACT_PRESENT, CheckOutcome.FAIL, Reason.CONTRACT_TERMS_MISMATCH)


def holds_locally(results: Sequence[CheckResult]) -> bool:
    """Any FAIL holds the loan; UNKNOWN is tolerated only for the two waiting reasons (SPEC 11.3)."""
    tolerated = {Reason.TITLE_PENDING_WINDOW, Reason.FIRST_PAYMENT_NOT_DUE, Reason.NO_TITLE_RECORD}
    return any(
        r.outcome == CheckOutcome.FAIL or (r.outcome == CheckOutcome.UNKNOWN and r.reason not in tolerated)
        for r in results
    )
