"""Planter of the race: one honest-looking loan uploaded to two banks at the same instant."""

from __future__ import annotations

from onepledge_datagen.application.kit import PlantingKit
from onepledge_datagen.application.planters.base import FRAUD, LOCKED_OR_CONFLICT
from onepledge_datagen.application.workspace import CaseRow

RACE_GROUP = "RACE"


class RacePairPlanter:
    """RACE_PAIR: the same row in S14 and S15; exactly one of the two locks."""

    case_type = "RACE_PAIR"

    def __init__(self, count: int, first_step: str = "S14", second_step: str = "S15") -> None:
        self._count = count
        self._steps = (first_step, second_step)

    def plant(self, kit: PlantingKit) -> None:
        """Title, payments and contract in order; the row appears unchanged in both tapes."""
        first, second = self._steps
        bank = kit.workspace.step(first).bank_code
        for _ in range(self._count):
            loan = kit.honest_loan(first)
            kit.plan_records(loan, bank)
            row = kit.loan_row(first, loan)
            twin = kit.copied_row(second, row)
            rows = [
                CaseRow("CONTENDER", row, LOCKED_OR_CONFLICT),
                CaseRow("CONTENDER", twin, LOCKED_OR_CONFLICT),
            ]
            kit.workspace.add_case(self.case_type, FRAUD, rows, race_group=RACE_GROUP)
