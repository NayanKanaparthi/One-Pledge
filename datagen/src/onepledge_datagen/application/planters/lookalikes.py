"""Planters of honest look-alikes that must raise no alert and no hold (SPEC 5.7)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from onepledge_datagen.application.kit import PlantingKit, RecordChoices
from onepledge_datagen.application.planters.base import HONEST_LOOKALIKE, RELEASED, locked
from onepledge_datagen.application.workspace import CaseRow, TapeRow
from onepledge_datagen.domain import catalog
from onepledge_datagen.domain.entities import LoanRecord
from onepledge_datagen.domain.randomness import RandomStream

NEAR_PAIR_LAST_ORIGINATION = date(2026, 7, 31)
BAND_1000_CENTS, BAND_100_CENTS = 100_000, 10_000
REFINANCE_FIRST, REFINANCE_LAST = date(2026, 11, 1), date(2026, 11, 10)
TITLE_PENDING_AGE_DAYS = (3, 15)


def _near_amount(rng: RandomStream, amount: int) -> int:
    """Same 1,000 dollar band, different 100 dollar band."""
    band = amount // BAND_1000_CENTS
    while True:
        candidate = band * BAND_1000_CENTS + rng.integer(0, BAND_1000_CENTS - 1)
        if candidate // BAND_100_CENTS != amount // BAND_100_CENTS:
            return candidate


class TwoCarsPlanter:
    """TWO_CARS_ONE_BORROWER: one Summit borrower on two cars, car A in S01, car B in S03 or S05."""

    case_type = "TWO_CARS_ONE_BORROWER"

    def __init__(self, pairs: int) -> None:
        self._pairs = pairs

    def plant(self, kit: PlantingKit) -> None:
        """Half the pairs (rounded up) are near: same month and 1,000 dollar band, other day and 100 band."""
        rng = kit.rng(f"planter:{self.case_type}")
        near_count = (self._pairs + 1) // 2
        b_steps = ("S03", "S05")
        for index in range(self._pairs):
            near = index < near_count
            last = NEAR_PAIR_LAST_ORIGINATION if near else None
            car_a = kit.honest_loan("S01", last=last)
            b_step = b_steps[index % 2]
            car_b = self._second_car(kit, rng, car_a, b_step, near)
            rows = []
            for role, step_id, loan in (("CAR_A", "S01", car_a), ("CAR_B", b_step, car_b)):
                kit.plan_records(loan, kit.workspace.step(step_id).bank_code)
                rows.append(CaseRow(role, kit.loan_row(step_id, loan), locked()))
            kit.workspace.add_case(self.case_type, HONEST_LOOKALIKE, rows)

    @staticmethod
    def _second_car(
        kit: PlantingKit, rng: RandomStream, car_a: LoanRecord, step_id: str, near: bool
    ) -> LoanRecord:
        window_first, window_last = kit.loans.honest_window(kit.workspace.step(step_id).as_of)
        if near:
            month_first = car_a.origination.replace(day=1)
            month_last = (month_first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            while True:
                day = rng.date_between(month_first, min(month_last, window_last))
                if day != car_a.origination:
                    break
            amount = _near_amount(rng, car_a.amount_cents)
        else:
            while True:
                day = rng.date_between(window_first, window_last)
                amount = kit.loans.amount_cents()
                different_month = (day.year, day.month) != (car_a.origination.year, car_a.origination.month)
                if different_month and amount // BAND_1000_CENTS != car_a.amount_cents // BAND_1000_CENTS:
                    break
        return kit.loans.create("SUMMIT", day, day, borrower_ref=car_a.borrower_ref, amount_cents=amount)


class MovedAfterReleasePlanter:
    """MOVED_AFTER_RELEASE: locked at Harbor in S01, released in S11, pledged unchanged at Northgate (S12)."""

    case_type = "MOVED_AFTER_RELEASE"

    def __init__(self, count: int) -> None:
        self._count = count

    def plant(self, kit: PlantingKit) -> None:
        """Release row with VIN and loan number, then the same row at the other bank."""
        for original in kit.workspace.take_baseline("S01", self._count):
            if original.loan is None:
                continue
            release = kit.release_row("S11", original.loan)
            repledge = kit.copied_row("S12", original)
            rows = [
                CaseRow("ORIGINAL", original, locked()),
                CaseRow("RELEASE", release, RELEASED),
                CaseRow("REPLEDGE", repledge, locked()),
            ]
            kit.workspace.add_case(self.case_type, HONEST_LOOKALIKE, rows)


class RefinancedPlanter:
    """REFINANCED: released in S11, then a new loan on the same car and borrower in S12 or S13."""

    case_type = "REFINANCED"

    def __init__(self, count: int) -> None:
        self._count = count

    def plant(self, kit: PlantingKit) -> None:
        """Half (rounded up) are pledged at Northgate in S12, the rest back at Harbor in S13."""
        rng = kit.rng(f"planter:{self.case_type}")
        at_northgate = (self._count + 1) // 2
        for index, original in enumerate(kit.workspace.take_baseline("S01", self._count)):
            old = original.loan
            if old is None:
                continue
            step_id = "S12" if index < at_northgate else "S13"
            new = replace(
                kit.loans.rekey(old),
                loan_number=kit.loans.codes.loan_number(old.lender_code),
                origination=rng.date_between(REFINANCE_FIRST, REFINANCE_LAST),
                amount_cents=kit.loans.amount_cents(),
                term_months=rng.pick(catalog.TERMS),
            )
            self._refinance_records(kit, old, new, kit.workspace.step(step_id).bank_code)
            rows = [
                CaseRow("ORIGINAL", original, locked()),
                CaseRow("RELEASE", kit.release_row("S11", old), RELEASED),
                CaseRow("NEW_LOAN", kit.loan_row(step_id, new), locked()),
            ]
            kit.workspace.add_case(self.case_type, HONEST_LOOKALIKE, rows)

    @staticmethod
    def _refinance_records(kit: PlantingKit, old: LoanRecord, new: LoanRecord, bank_code: str) -> None:
        plan = kit.workspace.plan(old.vin)
        plan.payments = [
            replace(stream, due_before=new.origination) if stream.loan.key == old.key else stream
            for stream in plan.payments
        ]
        kit.plan_records(new, bank_code, RecordChoices(title=False, may_skip_a_month=False))


class TitlePendingHonestPlanter:
    """TITLE_PENDING_HONEST: real new loans 3 to 15 days old with a contract and no title record yet."""

    case_type = "TITLE_PENDING_HONEST"

    def __init__(self, count: int, steps: tuple[str, ...] = ("S10", "S14", "S15")) -> None:
        self._count = count
        self._steps = steps

    def plant(self, kit: PlantingKit) -> None:
        """Split evenly over the steps."""
        rows: list[tuple[str, TapeRow]] = []
        for index in range(self._count):
            step_id = self._steps[index % len(self._steps)]
            step = kit.workspace.step(step_id)
            first = step.as_of - timedelta(days=TITLE_PENDING_AGE_DAYS[1])
            last = step.as_of - timedelta(days=TITLE_PENDING_AGE_DAYS[0])
            loan = kit.honest_loan(step_id, first, last)
            kit.plan_records(loan, step.bank_code, RecordChoices(title=False, may_skip_a_month=False))
            rows.append((step_id, kit.loan_row(step_id, loan)))
        for _, row in rows:
            kit.workspace.add_case(self.case_type, HONEST_LOOKALIKE, [CaseRow("LOAN", row, locked())])
