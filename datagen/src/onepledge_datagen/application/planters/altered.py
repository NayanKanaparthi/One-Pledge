"""Planters of the altered VIN copies (SPEC 5.7): check digit broken, no title, inside the title window."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from onepledge_datagen.application.kit import PlantingKit, RecordChoices
from onepledge_datagen.application.planters.base import (
    FRAUD,
    HOLDER,
    LIKELY,
    conflict,
    held,
    locked,
    rejected,
)
from onepledge_datagen.application.workspace import CaseRow, TapeRow
from onepledge_datagen.domain.randomness import RandomStream
from onepledge_datagen.domain.tape_format import COLUMN_VIN
from onepledge_datagen.domain.vin import (
    different_value_characters,
    is_valid_vin,
    replace_at,
    same_value_characters,
)

# Indexes of positions 4 to 8 and 11 to 17: never the manufacturer (1 to 3), check digit (9) or year (10).
ALTERABLE_INDEXES: tuple[int, ...] = (3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 15, 16)
NO_TITLE_MIN_AGE_DAYS = 60
PENDING_ORIGINATION_FIRST, PENDING_ORIGINATION_LAST = date(2026, 10, 2), date(2026, 10, 12)


def altered_vin(kit: PlantingKit, rng: RandomStream, vin: str, same_value: bool) -> str:
    """One character changed to the same transliteration value (still valid) or to a different one."""
    while True:
        index = rng.pick(ALTERABLE_INDEXES)
        options = same_value_characters(vin[index]) if same_value else different_value_characters(vin[index])
        if not options:
            continue
        candidate = replace_at(vin, index, rng.pick(options))
        if is_valid_vin(candidate) == same_value and kit.loans.vins.claim(candidate):
            return candidate


def _copy_with_vin(kit: PlantingKit, step_id: str, original: TapeRow, vin: str) -> TapeRow:
    cells = list(original.cells)
    cells[COLUMN_VIN] = vin
    loan = original.loan
    copy_loan = replace(kit.loans.rekey(loan), vin=vin) if loan is not None else None
    return kit.workspace.add_row(step_id, cells, copy_loan)


class AlteredCheckDigitPlanter:
    """COPY_VIN_ALTERED_CHECK_DIGIT_INVALID: one character changed to another value; cleaning rejects it."""

    case_type = "COPY_VIN_ALTERED_CHECK_DIGIT_INVALID"

    def __init__(self, count: int) -> None:
        self._count = count

    def plant(self, kit: PlantingKit) -> None:
        """Copy S01 rows into S09 with a broken check digit."""
        rng = kit.rng(f"planter:{self.case_type}")
        for original in kit.workspace.take_baseline("S01", self._count):
            vin = altered_vin(kit, rng, original.cells[COLUMN_VIN], same_value=False)
            copy = _copy_with_vin(kit, "S09", original, vin)
            rows = [
                CaseRow("ORIGINAL", original, locked()),
                CaseRow("COPY", copy, rejected("VIN_CHECK_DIGIT_INVALID")),
            ]
            kit.workspace.add_case(self.case_type, FRAUD, rows)


class AlteredNoTitlePlanter:
    """COPY_VIN_ALTERED_NO_TITLE: same value change, fabricated contract, no title and no payments."""

    case_type = "COPY_VIN_ALTERED_NO_TITLE"

    def __init__(self, count: int) -> None:
        self._count = count

    def plant(self, kit: PlantingKit) -> None:
        """Copy old S01 rows into S09 under a valid but different VIN."""
        rng = kit.rng(f"planter:{self.case_type}")
        as_of = kit.workspace.step("S09").as_of

        def old_enough(row: TapeRow) -> bool:
            """Originated more than the no-title minimum age before the S09 as-of date."""
            return row.loan is not None and (as_of - row.loan.origination).days > NO_TITLE_MIN_AGE_DAYS

        for original in kit.workspace.take_baseline("S01", self._count, old_enough):
            vin = altered_vin(kit, rng, original.cells[COLUMN_VIN], same_value=True)
            copy = _copy_with_vin(kit, "S09", original, vin)
            if copy.loan is not None:
                kit.add_contract(copy.loan, copy.loan.amount_cents, copy.loan.origination)
            failed = held(TITLE_EXISTS="NO_TITLE_RECORD", PAYMENTS_RECEIVED="NO_PAYMENT_IN_WINDOW")
            kit.workspace.add_case(
                self.case_type,
                FRAUD,
                [CaseRow("ORIGINAL", original, locked()), CaseRow("COPY", copy, failed)],
            )


class AlteredTitlePendingPlanter:
    """COPY_VIN_ALTERED_TITLE_PENDING: a new S07 loan copied into S09 under a same value VIN.

    The copy is inside the title window. Local checks cannot refute it (title
    pending, first payment not due, a fabricated contract), so it reaches the
    registry, which flags it by field agreement (Rule C).
    """

    case_type = "COPY_VIN_ALTERED_TITLE_PENDING"

    def __init__(self, count: int) -> None:
        self._count = count

    def plant(self, kit: PlantingKit) -> None:
        """Add the S07 originals with their records, then the S09 copies."""
        rng = kit.rng(f"planter:{self.case_type}")
        bank = kit.workspace.step("S07").bank_code
        for _ in range(self._count):
            loan = kit.honest_loan("S07", PENDING_ORIGINATION_FIRST, PENDING_ORIGINATION_LAST)
            kit.plan_records(loan, bank, RecordChoices(may_skip_a_month=False))
            original = kit.loan_row("S07", loan)
            vin = altered_vin(kit, rng, loan.vin, same_value=True)
            copy = _copy_with_vin(kit, "S09", original, vin)
            if copy.loan is not None:
                kit.add_contract(copy.loan, loan.amount_cents, loan.origination)
            include, exclude = ("BORROWER", "AMOUNT_EXACT", "DATE_DAY"), ("ASSET", "VIN")
            rows = [
                CaseRow("ORIGINAL", original, locked((HOLDER, LIKELY))),
                CaseRow("COPY", copy, conflict(LIKELY, "FIELDS", include, exclude)),
            ]
            kit.workspace.add_case(self.case_type, FRAUD, rows)
