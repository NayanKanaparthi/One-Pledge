"""Planters of the copy cases: exact copy, amount changed with and without a second contract, date changed."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from onepledge_datagen.application.kit import PlantingKit
from onepledge_datagen.application.planters.base import (
    ALL_KINDS,
    EXACT,
    FRAUD,
    HOLDER,
    LIKELY,
    conflict,
    held,
    locked,
)
from onepledge_datagen.application.workspace import CaseRow, TapeRow
from onepledge_datagen.domain.tape_format import (
    COLUMN_AMOUNT,
    COLUMN_BALANCE,
    COLUMN_ORIGINATION,
    balance_cents,
)

MIN_SHIFT_CENTS, MAX_SHIFT_CENTS = 50_000, 300_000
MIN_DATE_SHIFT_DAYS, MAX_DATE_SHIFT_DAYS = 7, 60


class ExactCopyPlanter:
    """EXACT_COPY: an S01 row copied unchanged into Northgate's back book and its S08 pledge."""

    case_type = "EXACT_COPY"

    def __init__(self, back_book_count: int, pledge_count: int) -> None:
        self._targets = (("S03", back_book_count), ("S08", pledge_count))

    def plant(self, kit: PlantingKit) -> None:
        """Copy each original's row verbatim."""
        for step_id, count in self._targets:
            for original in kit.workspace.take_baseline("S01", count):
                copy = kit.copied_row(step_id, original)
                rows = [
                    CaseRow("ORIGINAL", original, locked((HOLDER, EXACT))),
                    CaseRow("COPY", copy, conflict(EXACT, "ASSET", ALL_KINDS)),
                ]
                kit.workspace.add_case(self.case_type, FRAUD, rows)


class AmountChangedPlanter:
    """COPY_AMOUNT_CHANGED: an S01 row copied into S09 with the amount moved by 500 to 3,000 dollars.

    The lender also deposits a second contract with the changed amount, so the bank's
    contract check passes and the registry catches the copy on the asset code.
    """

    case_type = "COPY_AMOUNT_CHANGED"
    second_contract = True

    def __init__(self, count: int) -> None:
        self._count = count

    def plant(self, kit: PlantingKit) -> None:
        """Change amount and balance of each copy."""
        rng = kit.rng(f"planter:{self.case_type}")
        fmt = kit.workspace.lender_format("S09")
        for original in kit.workspace.take_baseline("S01", self._count):
            loan = original.loan
            if loan is None:
                continue
            shift = rng.integer(MIN_SHIFT_CENTS, MAX_SHIFT_CENTS) * (1 if rng.chance(1, 2) else -1)
            amount = loan.amount_cents + shift
            balance = max(1, min(amount, balance_cents(original.cells) * amount // loan.amount_cents))
            cells = list(original.cells)
            cells[COLUMN_AMOUNT] = fmt.format_amount(amount)
            cells[COLUMN_BALANCE] = fmt.format_amount(balance)
            copy_loan = replace(kit.loans.rekey(loan), amount_cents=amount)
            copy = kit.workspace.add_row("S09", cells, copy_loan)
            if self.second_contract:
                kit.add_contract(loan, amount, loan.origination)
            kit.workspace.add_case(self.case_type, FRAUD, self._rows(original, copy))

    def _rows(self, original: TapeRow, copy: TapeRow) -> list[CaseRow]:
        return [
            CaseRow("ORIGINAL", original, locked((HOLDER, LIKELY))),
            CaseRow(
                "COPY",
                copy,
                conflict(
                    LIKELY,
                    "ASSET",
                    ("ASSET", "VIN", "BORROWER", "DATE_DAY", "DATE_MONTH"),
                    ("AMOUNT_EXACT", "AMOUNT_100"),
                ),
            ),
        ]


class AmountChangedNoContractPlanter(AmountChangedPlanter):
    """COPY_AMOUNT_CHANGED_NO_CONTRACT: as above with no second contract, so the bank holds the copy."""

    case_type = "COPY_AMOUNT_CHANGED_NO_CONTRACT"
    second_contract = False

    def _rows(self, original: TapeRow, copy: TapeRow) -> list[CaseRow]:
        return [
            CaseRow("ORIGINAL", original, locked()),
            CaseRow("COPY", copy, held(CONTRACT_PRESENT="CONTRACT_TERMS_MISMATCH")),
        ]


class DateChangedPlanter:
    """COPY_DATE_CHANGED: an S03 row copied into S09 with the origination moved 7 to 60 days."""

    case_type = "COPY_DATE_CHANGED"

    def __init__(self, count: int) -> None:
        self._count = count

    def plant(self, kit: PlantingKit) -> None:
        """Move the date (never after S09's as-of date) and add the lender's second contract."""
        rng = kit.rng(f"planter:{self.case_type}")
        fmt = kit.workspace.lender_format("S09")
        as_of = kit.workspace.step("S09").as_of
        for original in kit.workspace.take_baseline("S03", self._count):
            loan = original.loan
            if loan is None:
                continue
            days = rng.integer(MIN_DATE_SHIFT_DAYS, MAX_DATE_SHIFT_DAYS)
            later = loan.origination + timedelta(days=days)
            moved = later if later <= as_of and rng.chance(1, 2) else loan.origination - timedelta(days=days)
            cells = list(original.cells)
            cells[COLUMN_ORIGINATION] = fmt.format_date(moved)
            copy = kit.workspace.add_row("S09", cells, replace(kit.loans.rekey(loan), origination=moved))
            kit.add_contract(loan, loan.amount_cents, moved)
            include = ("ASSET", "VIN", "BORROWER", "AMOUNT_EXACT", "AMOUNT_100", "AMOUNT_1000")
            rows = [
                CaseRow("ORIGINAL", original, locked((HOLDER, LIKELY))),
                CaseRow("COPY", copy, conflict(LIKELY, "ASSET", include, ("DATE_DAY",))),
            ]
            kit.workspace.add_case(self.case_type, FRAUD, rows)
