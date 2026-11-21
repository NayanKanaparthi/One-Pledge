"""Planters of cars that do not exist: an invented loan outside and inside the title window."""

from __future__ import annotations

from datetime import timedelta

from onepledge_datagen.application.kit import PlantingKit, RecordChoices
from onepledge_datagen.application.planters.base import BLIND_SPOT, FRAUD, held, locked
from onepledge_datagen.application.workspace import CaseRow

INVENTED_AGE_DAYS = (120, 400)
BLIND_SPOT_AGE_DAYS = (5, 20)
CONTRACT_ONLY = RecordChoices(title=False, payments=False, contract=True)


class InventedNoTitlePlanter:
    """INVENTED_NO_TITLE: a fresh VIN with a fabricated contract, no title and no payments."""

    case_type = "INVENTED_NO_TITLE"

    def __init__(self, count: int, step_id: str = "S10") -> None:
        self._count = count
        self._step_id = step_id

    def plant(self, kit: PlantingKit) -> None:
        """Originated 120 to 400 days before the step's as-of date."""
        as_of = kit.workspace.step(self._step_id).as_of
        bank = kit.workspace.step(self._step_id).bank_code
        first, last = (
            as_of - timedelta(days=INVENTED_AGE_DAYS[1]),
            as_of - timedelta(days=INVENTED_AGE_DAYS[0]),
        )
        for _ in range(self._count):
            loan = kit.honest_loan(self._step_id, first, last)
            kit.plan_records(loan, bank, CONTRACT_ONLY)
            row = kit.loan_row(self._step_id, loan)
            expected = held(TITLE_EXISTS="NO_TITLE_RECORD", PAYMENTS_RECEIVED="NO_PAYMENT_IN_WINDOW")
            kit.workspace.add_case(self.case_type, FRAUD, [CaseRow("LOAN", row, expected)])


class BlindSpotPlanter:
    """BLIND_SPOT_INVENTED_IN_PENDING_WINDOW: an invented car 5 to 20 days old; layer 1 locks it (SPEC 23)."""

    case_type = "BLIND_SPOT_INVENTED_IN_PENDING_WINDOW"

    def __init__(self, count: int, step_id: str = "S10") -> None:
        self._count = count
        self._step_id = step_id

    def plant(self, kit: PlantingKit) -> None:
        """No title, no payments, a fabricated contract."""
        as_of = kit.workspace.step(self._step_id).as_of
        bank = kit.workspace.step(self._step_id).bank_code
        first, last = (
            as_of - timedelta(days=BLIND_SPOT_AGE_DAYS[1]),
            as_of - timedelta(days=BLIND_SPOT_AGE_DAYS[0]),
        )
        for _ in range(self._count):
            loan = kit.honest_loan(self._step_id, first, last)
            kit.plan_records(loan, bank, CONTRACT_ONLY)
            row = kit.loan_row(self._step_id, loan)
            kit.workspace.add_case(self.case_type, BLIND_SPOT, [CaseRow("LOAN", row, locked())])
