"""Planters of real cars that fail one local check: another lienholder, no payment for 90 days."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from onepledge_datagen.application.kit import PlantingKit, RecordChoices
from onepledge_datagen.application.planters.base import FRAUD, held
from onepledge_datagen.application.workspace import CaseRow
from onepledge_datagen.domain import catalog

NO_PAYMENT_MIN_AGE_DAYS = 240
LAST_PAYMENT_DAYS_BEFORE = (100, 160)


class OtherLienholderPlanter:
    """TITLE_OTHER_LIENHOLDER: payments and a Summit contract, but the title names another lender."""

    case_type = "TITLE_OTHER_LIENHOLDER"

    def __init__(self, count: int, step_id: str = "S10") -> None:
        self._count = count
        self._step_id = step_id

    def plant(self, kit: PlantingKit) -> None:
        """Honest loan whose title lienholder is Crestline Auto Credit Corp."""
        bank = kit.workspace.step(self._step_id).bank_code
        choices = RecordChoices(lienholder=catalog.OTHER_LIENHOLDER, may_skip_a_month=False)
        for _ in range(self._count):
            loan = kit.honest_loan(self._step_id)
            kit.plan_records(loan, bank, choices)
            row = kit.loan_row(self._step_id, loan)
            expected = held(LIENHOLDER_MATCH="LIENHOLDER_MISMATCH")
            kit.workspace.add_case(self.case_type, FRAUD, [CaseRow("LOAN", row, expected)])


class NoPaymentsPlanter:
    """NO_PAYMENTS_90D: title and contract in order, last payment 100 to 200 days before the as-of date."""

    case_type = "NO_PAYMENTS_90D"

    def __init__(self, count: int, step_id: str = "S10") -> None:
        self._count = count
        self._step_id = step_id

    def plant(self, kit: PlantingKit) -> None:
        """Payments stop well before the payment window."""
        rng = kit.rng(f"planter:{self.case_type}")
        step = kit.workspace.step(self._step_id)
        last_origination = step.as_of - timedelta(days=NO_PAYMENT_MIN_AGE_DAYS)
        for _ in range(self._count):
            loan = kit.honest_loan(self._step_id, catalog.ORIGINATION_FLOOR, last_origination)
            kit.plan_records(loan, step.bank_code, RecordChoices(may_skip_a_month=False))
            cutoff = step.as_of - timedelta(days=rng.integer(*LAST_PAYMENT_DAYS_BEFORE))
            plan = kit.workspace.plan(loan.vin)
            plan.payments = [replace(stream, paid_on_or_before=cutoff) for stream in plan.payments]
            row = kit.loan_row(self._step_id, loan)
            expected = held(PAYMENTS_RECEIVED="NO_PAYMENT_IN_WINDOW")
            kit.workspace.add_case(self.case_type, FRAUD, [CaseRow("LOAN", row, expected)])
