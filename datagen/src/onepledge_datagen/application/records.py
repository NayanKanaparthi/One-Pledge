"""Outside records built from the vehicle plans (SPEC 5.4): titles, monthly payments and contracts."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from onepledge_datagen.application.workspace import PaymentStream, VehiclePlan
from onepledge_datagen.domain.amortisation import monthly_payment_cents
from onepledge_datagen.domain.calendar import add_months
from onepledge_datagen.domain.entities import ContractRecord, PaymentRecord, TitleRecord
from onepledge_datagen.domain.randomness import RandomStream

FIRST_DUE_DAYS = 30
JITTER_EARLY, JITTER_LATE = 3, 5
MIN_DUES_FOR_A_SKIP = 4


def due_dates(stream: PaymentStream, until: date) -> list[date]:
    """Monthly due dates from origination plus 30 days, within the term and up to `until`."""
    first = stream.loan.origination + timedelta(days=FIRST_DUE_DAYS)
    dues: list[date] = []
    for month in range(stream.loan.term_months):
        due = add_months(first, month)
        if due > until or (stream.due_before is not None and due >= stream.due_before):
            break
        dues.append(due)
    return dues


class OutsideRecordsBuilder:
    """Turns vehicle plans into the three record lists, in a deterministic order."""

    def __init__(self, rng: RandomStream, timeline_end: date) -> None:
        self._rng = rng
        self._end = timeline_end

    def build(
        self, plans: dict[str, VehiclePlan]
    ) -> tuple[list[TitleRecord], list[PaymentRecord], list[ContractRecord]]:
        """Titles sorted by VIN; payments by VIN, date and id; contracts by VIN, date and id."""
        titles: list[TitleRecord] = []
        payments: list[PaymentRecord] = []
        contracts: list[ContractRecord] = []
        for vin in sorted(plans):
            plan = plans[vin]
            if plan.title is not None:
                titles.append(
                    TitleRecord(
                        vin,
                        plan.title.state,
                        "CLEAN",
                        plan.title.lienholder_name,
                        plan.title.lien_recorded_on,
                    )
                )
            for stream in plan.payments:
                payments.extend(self._payments(vin, stream))
            for spec in plan.contracts:
                contract_id = self._rng.new_uuid()
                digest = hashlib.sha256(f"contract:{contract_id}".encode()).hexdigest()
                contracts.append(
                    ContractRecord(
                        contract_id, vin, spec.lender_name, spec.amount_cents, spec.signed_on, digest
                    )
                )
        payments.sort(key=lambda p: (p.vin, p.paid_on, p.payment_id))
        contracts.sort(key=lambda c: (c.vin, c.signed_on, c.contract_id))
        return titles, payments, contracts

    def _payments(self, vin: str, stream: PaymentStream) -> list[PaymentRecord]:
        loan = stream.loan
        amount = monthly_payment_cents(loan.amount_cents, loan.apr_tenths, loan.term_months)
        dues = due_dates(stream, self._end)
        skipped = (
            self._rng.integer(1, len(dues) - 1)
            if stream.skips_one_month and len(dues) >= MIN_DUES_FOR_A_SKIP
            else None
        )
        records = []
        for index, due in enumerate(dues):
            paid = due + timedelta(days=self._rng.integer(-JITTER_EARLY, JITTER_LATE))
            payment_id = self._rng.new_uuid()
            if index == skipped or paid > self._end:
                continue
            if stream.paid_on_or_before is not None and paid > stream.paid_on_or_before:
                continue
            records.append(PaymentRecord(payment_id, vin, paid, amount, stream.deposit_bank_code))
        return records
