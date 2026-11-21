"""Data quality rows of SPEC 5.7: fixable changes to honest rows, unfixable extra rows, totals rows."""

from __future__ import annotations

import re
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
    CENTS_PER_DOLLAR,
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
from onepledge_datagen.domain.text import plural
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

    def whole_dollar_counts(self) -> dict[str, int]:
        """Baseline loans per fixable step that need an amount without cents (whole dollar forms)."""
        needed = sum(
            count
            for form, count in zip(
                AMOUNT_FORMS, expected_form_counts(self.fixable.get(AMOUNT_KIND, 0)), strict=True
            )
            if form.cents_rule is _whole_dollars
        )
        counts = dict.fromkeys(self.fixable_steps, 0)
        for index in range(needed):
            counts[self.fixable_steps[index % len(self.fixable_steps)]] += 1
        return {step: count for step, count in counts.items() if count}


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
    return True


@dataclass(frozen=True)
class AmountForm:
    """One of the three alternative amount spellings of SPEC 5.7 and the amounts it can spell."""

    name: str
    spell: Callable[[int], str]
    pattern: re.Pattern[str]
    cents_rule: Callable[[int], bool]


def _whole_dollars(cents: int) -> bool:
    return cents % CENTS_PER_DOLLAR == 0


def _tenths_of_a_dollar(cents: int) -> bool:
    return cents % 10 == 0 and cents % CENTS_PER_DOLLAR != 0


AMOUNT_KIND = "M_AMOUNT_ALT_FORMAT"
# Assigned in rotation in the order the rows are chosen, so the three forms are planted
# in the proportions (n+2)//3, (n+1)//3 and n//3 for n rows of the kind.
AMOUNT_FORMS: tuple[AmountForm, ...] = (
    AmountForm("23450", amount_without_decimals, re.compile(r"[0-9]+", re.ASCII), _whole_dollars),
    AmountForm(
        "$23,450",
        dollar_amount_without_cents,
        re.compile(r"\$[0-9]{1,3}(,[0-9]{3})*", re.ASCII),
        _whole_dollars,
    ),
    AmountForm(
        "23450.5", amount_with_one_decimal, re.compile(r"[0-9]+\.[0-9]", re.ASCII), _tenths_of_a_dollar
    ),
)


def expected_form_counts(rows: int) -> list[int]:
    """How many rows of each amount form a kind of `rows` rows holds, in AMOUNT_FORMS order."""
    return [len(range(index, rows, len(AMOUNT_FORMS))) for index in range(len(AMOUNT_FORMS))]


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


def _amount_writer(form: AmountForm) -> Callable[[RowEdit], None]:
    def write(edit: RowEdit) -> None:
        """Respell the original amount in this form."""
        edit.cells[COLUMN_AMOUNT] = form.spell(edit.loan.amount_cents)

    return write


def _lower_borrower(edit: RowEdit) -> None:
    edit.cells[COLUMN_BORROWER] = edit.cells[COLUMN_BORROWER].lower()


FIXERS: Mapping[str, Callable[[RowEdit], None]] = MappingProxyType(
    {
        "M_VIN_LOWERCASE": _lower_vin,
        "M_VIN_SEPARATORS": _separate_vin,
        "M_VIN_OIQ": _letter_for_digit,
        "M_WHITESPACE": _pad_cells,
        "M_DATE_ALT_FORMAT": _spell_date,
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


def _amount_assignments(
    pool: list[TapeRow], count: int, rng: RandomStream
) -> list[tuple[TapeRow, Callable[[RowEdit], None]]]:
    """`count` rows for M_AMOUNT_ALT_FORMAT, the forms of AMOUNT_FORMS taken in rotation."""
    assignments: list[tuple[TapeRow, Callable[[RowEdit], None]]] = []
    used: set[int] = set()
    for index in range(count):
        form = AMOUNT_FORMS[index % len(AMOUNT_FORMS)]
        suitable = [
            row
            for row in pool
            if row.loan is not None and row.row_id not in used and form.cents_rule(row.loan.amount_cents)
        ]
        if not suitable:
            raise PlanError(f"No unused row has an amount that can be written as {form.name}.")
        row = rng.pick(suitable)
        used.add(row.row_id)
        assignments.append((row, _amount_writer(form)))
    return assignments


class MessinessApplier:
    """Plants every data quality row of a profile."""

    def __init__(self, plan: MessinessPlan) -> None:
        self._plan = plan
        self._reserved: list[tuple[TapeRow, Callable[[RowEdit], None]]] = []

    def reserve(self, kit: PlantingKit) -> None:
        """Before the cases are planted, take the M_AMOUNT_ALT_FORMAT rows out of the baseline.

        The whole dollar loans the portfolio made for them cannot then be used by a case.
        """
        count = self._plan.fixable.get(AMOUNT_KIND, 0)
        if not count:
            return
        workspace = kit.workspace
        pool = [row for step in self._plan.fixable_steps for row in workspace.baseline_rows[step]]
        self._reserved = _amount_assignments(pool, count, kit.rng("messiness-amounts"))
        reserved_ids = {row.row_id for row, _ in self._reserved}
        for step in self._plan.fixable_steps:
            workspace.baseline_rows[step] = [
                r for r in workspace.baseline_rows[step] if r.row_id not in reserved_ids
            ]

    def apply(self, kit: PlantingKit) -> MessinessResult:
        """Fixable kinds on unused honest rows, then the unfixable extra rows."""
        rng = kit.rng("messiness")
        self._fixable(kit, rng)
        return self._unfixable(kit, rng)

    def _fixable(self, kit: PlantingKit, rng: RandomStream) -> None:
        workspace = kit.workspace
        for kind, count in self._plan.fixable.items():
            pool = [row for step in self._plan.fixable_steps for row in workspace.baseline_rows[step]]
            if kind == AMOUNT_KIND:
                assignments = self._reserved or _amount_assignments(pool, count, rng)
            else:
                suitable = [row for row in pool if _suits(kind, row)]
                if len(suitable) < count:
                    raise PlanError(f"Only {plural(len(suitable), 'row')} suit {kind}; {count:,} are needed.")
                chosen = sorted(rng.sample(suitable, count), key=lambda item: item.row_id)
                assignments = [(row, FIXERS[kind]) for row in chosen]
            chosen_ids = {row.row_id for row, _ in assignments}
            for step in self._plan.fixable_steps:
                workspace.baseline_rows[step] = [
                    r for r in workspace.baseline_rows[step] if r.row_id not in chosen_ids
                ]
            for row, fixer in assignments:
                fixer(_edit(kit, row, rng))
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
