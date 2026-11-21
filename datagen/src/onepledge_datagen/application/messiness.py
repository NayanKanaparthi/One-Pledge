"""Data quality rows of SPEC 5.7: fixable changes to honest rows, unfixable extra rows, totals rows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import MappingProxyType

from onepledge_datagen.application.kit import PlantingKit
from onepledge_datagen.application.workspace import PlantedMessiness, TapeRow
from onepledge_datagen.domain.calendar import day_month_year
from onepledge_datagen.domain.entities import LoanRecord
from onepledge_datagen.domain.errors import PlanError
from onepledge_datagen.domain.money import (
    amount_with_one_decimal,
    amount_without_decimals,
    dollar_amount_without_cents,
    plain_amount,
)
from onepledge_datagen.domain.randomness import RandomStream
from onepledge_datagen.domain.tape_format import (
    COLUMN_AMOUNT,
    COLUMN_BALANCE,
    COLUMN_BORROWER,
    COLUMN_LOAN_NUMBER,
    COLUMN_ORIGINATION,
    COLUMN_VIN,
    LenderFormat,
)
from onepledge_datagen.domain.vin import TRANSLITERATION, is_valid_vin

FIXABLE: Mapping[str, str] = MappingProxyType(
    {
        "M_VIN_LOWERCASE": "VIN_CASE_NORMALISED",
        "M_VIN_SEPARATORS": "VIN_SEPARATORS_REMOVED",
        "M_VIN_OIQ": "VIN_CHARACTER_SUBSTITUTED",
        "M_WHITESPACE": "WHITESPACE_TRIMMED",
        "M_DATE_ALT_FORMAT": "DATE_FORMAT_NORMALISED",
        "M_AMOUNT_ALT_FORMAT": "AMOUNT_FORMAT_NORMALISED",
        "M_BORROWER_LOWERCASE": "BORROWER_REF_NORMALISED",
    }
)
UNFIXABLE: Mapping[str, str] = MappingProxyType(
    {
        "M_VIN_MISSING": "MISSING_REQUIRED_FIELD",
        "M_VIN_SHORT": "VIN_LENGTH_INVALID",
        "M_VIN_TYPO": "VIN_CHECK_DIGIT_INVALID",
        "M_DATE_GARBAGE": "DATE_UNPARSEABLE",
        "M_DATE_FUTURE": "DATE_IN_FUTURE",
        "M_AMOUNT_GARBAGE": "AMOUNT_UNPARSEABLE",
        "M_AMOUNT_NEGATIVE": "AMOUNT_OUT_OF_RANGE",
        "M_BALANCE_GT_ORIGINAL": "BALANCE_EXCEEDS_ORIGINAL",
        "M_FORMULA_CELL": "UNSAFE_CHARACTERS",
        "M_DUPLICATE_ROW": "DUPLICATE_VIN_IN_FILE",
    }
)
TOTALS_KIND, TOTALS_PROBLEM = "M_TOTALS_ROW", "NON_DATA_ROW"
SERIAL_INDEXES = range(11, 17)


@dataclass(frozen=True)
class MessinessPlan:
    """How many rows of each kind, and where unfixable rows may go."""

    fixable: dict[str, int]
    unfixable: dict[str, int]
    fixable_steps: tuple[str, ...]
    unfixable_steps: tuple[str, ...]


@dataclass
class DuplicatePair:
    """The two identical rows of an M_DUPLICATE_ROW; the later one is the planted row."""

    first: TapeRow
    second: TapeRow


@dataclass
class MessinessResult:
    """Rows changed or added; duplicate pairs are resolved after the rows are shuffled."""

    duplicates: list[DuplicatePair] = field(default_factory=list)


def _suits(kind: str, row: TapeRow) -> bool:
    if row.loan is None:
        return False
    if kind == "M_VIN_OIQ":
        return any(char in "01" for char in row.cells[COLUMN_VIN])
    if kind == "M_AMOUNT_ALT_FORMAT":
        return row.loan.amount_cents % 10 == 0
    return True


@dataclass(frozen=True)
class RowEdit:
    """One row being changed, with what a change may need."""

    cells: list[str]
    loan: LoanRecord
    fmt: LenderFormat
    rng: RandomStream
    as_of: date


def _lower_vin(edit: RowEdit) -> None:
    edit.cells[COLUMN_VIN] = edit.cells[COLUMN_VIN].lower()


def _separate_vin(edit: RowEdit) -> None:
    vin, separator = edit.cells[COLUMN_VIN], edit.rng.pick(("-", " "))
    edit.cells[COLUMN_VIN] = f"{vin[:3]}{separator}{vin[3:8]}{separator}{vin[8:]}"


def _letter_for_digit(edit: RowEdit) -> None:
    vin = edit.cells[COLUMN_VIN]
    index = edit.rng.pick([i for i, char in enumerate(vin) if char in "01"])
    edit.cells[COLUMN_VIN] = vin[:index] + ("O" if vin[index] == "0" else "I") + vin[index + 1 :]


def _pad_cells(edit: RowEdit) -> None:
    for column in (COLUMN_VIN, COLUMN_BORROWER, COLUMN_ORIGINATION):
        edit.cells[column] = f" {edit.cells[column]}  "


def _spell_date(edit: RowEdit) -> None:
    edit.cells[COLUMN_ORIGINATION] = day_month_year(edit.loan.origination)


def _respell_amount(edit: RowEdit) -> None:
    cents = edit.loan.amount_cents
    options: tuple[Callable[[int], str], ...] = (
        (amount_without_decimals, dollar_amount_without_cents)
        if cents % 100 == 0
        else (amount_with_one_decimal,)
    )
    edit.cells[COLUMN_AMOUNT] = edit.rng.pick(options)(cents)


def _lower_borrower(edit: RowEdit) -> None:
    edit.cells[COLUMN_BORROWER] = edit.cells[COLUMN_BORROWER].lower()


FIXERS: Mapping[str, Callable[[RowEdit], None]] = MappingProxyType(
    {
        "M_VIN_LOWERCASE": _lower_vin,
        "M_VIN_SEPARATORS": _separate_vin,
        "M_VIN_OIQ": _letter_for_digit,
        "M_WHITESPACE": _pad_cells,
        "M_DATE_ALT_FORMAT": _spell_date,
        "M_AMOUNT_ALT_FORMAT": _respell_amount,
        "M_BORROWER_LOWERCASE": _lower_borrower,
    }
)


def _drop_vin(edit: RowEdit) -> None:
    edit.cells[COLUMN_VIN] = ""


def _shorten_vin(edit: RowEdit) -> None:
    edit.cells[COLUMN_VIN] = edit.cells[COLUMN_VIN][:-1]


def _swap_vin_characters(edit: RowEdit) -> None:
    edit.cells[COLUMN_VIN] = _typo(edit.cells[COLUMN_VIN], edit.rng)


def _garbage_date(edit: RowEdit) -> None:
    edit.cells[COLUMN_ORIGINATION] = "13/45/2026"


def _future_date(edit: RowEdit) -> None:
    edit.cells[COLUMN_ORIGINATION] = edit.fmt.format_date(
        edit.as_of + timedelta(days=edit.rng.integer(5, 40))
    )


def _garbage_amount(edit: RowEdit) -> None:
    edit.cells[COLUMN_AMOUNT] = "N/A"


def _negative_amount(edit: RowEdit) -> None:
    edit.cells[COLUMN_AMOUNT] = "-" + plain_amount(edit.loan.amount_cents)


def _balance_above_original(edit: RowEdit) -> None:
    edit.cells[COLUMN_BALANCE] = edit.fmt.format_amount(edit.loan.amount_cents + edit.rng.integer(1, 500_000))


def _formula_cell(edit: RowEdit) -> None:
    if edit.rng.chance(1, 2):
        edit.cells[COLUMN_LOAN_NUMBER] = '=HYPERLINK("http://x","y")'
    else:
        edit.cells[COLUMN_BORROWER] = "@SUM(1+1)"


BREAKERS: Mapping[str, Callable[[RowEdit], None]] = MappingProxyType(
    {
        "M_VIN_MISSING": _drop_vin,
        "M_VIN_SHORT": _shorten_vin,
        "M_VIN_TYPO": _swap_vin_characters,
        "M_DATE_GARBAGE": _garbage_date,
        "M_DATE_FUTURE": _future_date,
        "M_AMOUNT_GARBAGE": _garbage_amount,
        "M_AMOUNT_NEGATIVE": _negative_amount,
        "M_BALANCE_GT_ORIGINAL": _balance_above_original,
        "M_FORMULA_CELL": _formula_cell,
    }
)


def _typo(vin: str, rng: RandomStream) -> str:
    while True:
        index = rng.pick(list(SERIAL_INDEXES[:-1]))
        left, right = vin[index], vin[index + 1]
        if TRANSLITERATION[left] == TRANSLITERATION[right]:
            continue
        swapped = vin[:index] + right + left + vin[index + 2 :]
        if not is_valid_vin(swapped):
            return swapped


class MessinessApplier:
    """Plants every data quality row of a profile."""

    def __init__(self, plan: MessinessPlan) -> None:
        self._plan = plan

    def apply(self, kit: PlantingKit) -> MessinessResult:
        """Fixable kinds on unused honest rows, then the unfixable extra rows."""
        rng = kit.rng("messiness")
        self._fixable(kit, rng)
        return self._unfixable(kit, rng)

    def _fixable(self, kit: PlantingKit, rng: RandomStream) -> None:
        workspace = kit.workspace
        for kind, count in self._plan.fixable.items():
            pool = [row for step in self._plan.fixable_steps for row in workspace.baseline_rows[step]]
            suitable = [row for row in pool if _suits(kind, row)]
            if len(suitable) < count:
                raise PlanError(f"Only {len(suitable)} rows suit {kind}; {count} are needed.")
            chosen = rng.sample(suitable, count)
            chosen_ids = {row.row_id for row in chosen}
            for step in self._plan.fixable_steps:
                workspace.baseline_rows[step] = [
                    r for r in workspace.baseline_rows[step] if r.row_id not in chosen_ids
                ]
            for row in sorted(chosen, key=lambda item: item.row_id):
                FIXERS[kind](_edit(kit, row, rng))
                workspace.messiness.append(PlantedMessiness(kind, row, "FIXED", FIXABLE[kind]))

    def _unfixable(self, kit: PlantingKit, rng: RandomStream) -> MessinessResult:
        result = MessinessResult()
        placements = [kind for kind, count in self._plan.unfixable.items() for _ in range(count)]
        steps = self._plan.unfixable_steps
        for index, kind in enumerate(placements):
            step_id = steps[index % len(steps)]
            if kind == "M_DUPLICATE_ROW":
                result.duplicates.append(self._duplicate(kit, step_id))
                continue
            loan = kit.honest_loan(step_id)
            row = kit.loan_row(step_id, loan)
            BREAKERS[kind](_edit(kit, row, rng))
            kit.workspace.messiness.append(PlantedMessiness(kind, row, "REJECTED", UNFIXABLE[kind]))
        return result

    @staticmethod
    def _duplicate(kit: PlantingKit, step_id: str) -> DuplicatePair:
        loan = kit.honest_loan(step_id)
        kit.plan_records(loan, kit.workspace.step(step_id).bank_code)
        first = kit.loan_row(step_id, loan)
        return DuplicatePair(first, kit.copied_row(step_id, first))


def _edit(kit: PlantingKit, row: TapeRow, rng: RandomStream) -> RowEdit:
    if row.loan is None:
        raise PlanError("A planted data quality row has no loan.")
    step = kit.workspace.step(row.step_id)
    return RowEdit(row.cells, row.loan, kit.workspace.lender_format(row.step_id), rng, step.as_of)
