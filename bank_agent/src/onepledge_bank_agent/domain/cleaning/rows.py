"""`clean_row`: the row and field rules of SPEC 10.4 in the order of SPEC_DECISIONS 13.

1. Row rules first: NON_DATA_ROW, then COLUMN_COUNT_MISMATCH. The first that applies
   rejects the row with that one issue and no field rule runs.
2. Every required field is evaluated on its own, so one row can carry several issues.
3. Within a field: trim, empty, unsafe characters, then the field's own rules; the
   first REJECTED issue ends that field, FIXED issues found before it are kept.

A row is its cells in file order, exactly as parsed (SPEC_DECISIONS 25 rule 4): the
totals rule reads `cells[0]` and the count rule compares the number of cells with the
number of header cells.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date

from onepledge_bank_agent.domain.cleaning.amounts import balance_rules, clean_amount
from onepledge_bank_agent.domain.cleaning.dates import clean_origination_date
from onepledge_bank_agent.domain.cleaning.header import (
    BORROWER_REF,
    CURRENT_BALANCE,
    LOAN_NUMBER,
    ORIGINAL_AMOUNT,
    ORIGINATION_DATE,
    ROW,
    VIN,
    HeaderMap,
)
from onepledge_bank_agent.domain.cleaning.model import (
    CleanedRow,
    FieldResult,
    LoanValues,
    RejectedRow,
    fixed,
    rejected,
)
from onepledge_bank_agent.domain.cleaning.text_fields import clean_borrower_ref, clean_loan_number
from onepledge_bank_agent.domain.cleaning.vin_field import clean_vin
from onepledge_bank_agent.domain.enums import CleaningAction, CleaningProblem, TapeKind
from onepledge_bank_agent.domain.text_rules import ascii_upper, split_ascii_whitespace, trim
from onepledge_bank_agent.domain.values import CleaningIssue, LoanTerms

NON_DATA_MARKERS = frozenset({"TOTAL", "TOTALS", "GRAND TOTAL", "SUBTOTAL"})
UNSAFE_LEADING = frozenset("=+@")
CONTROL_LIMIT = 0x20
DELETE = 0x7F
FIELD_ORDER: tuple[str, ...] = (
    ROW,
    LOAN_NUMBER,
    VIN,
    BORROWER_REF,
    ORIGINATION_DATE,
    ORIGINAL_AMOUNT,
    CURRENT_BALANCE,
)


def _issue_order(issue: CleaningIssue) -> tuple[int, str]:
    return FIELD_ORDER.index(issue.field), issue.problem.value


def has_control_character(raw: str) -> bool:
    """Any character U+0000 to U+001F or U+007F, anywhere in the cell as sent (SPEC_DECISIONS 82)."""
    return any(ord(char) < CONTROL_LIMIT or ord(char) == DELETE for char in raw)


def row_rule(cells: Sequence[str], header_map: HeaderMap) -> CleaningIssue | None:
    """The one row issue that rejects the whole row, if any."""
    if not any(trim(cell) for cell in cells):
        return rejected(ROW, CleaningProblem.NON_DATA_ROW)
    if ascii_upper(" ".join(split_ascii_whitespace(cells[0]))) in NON_DATA_MARKERS:
        return rejected(ROW, CleaningProblem.NON_DATA_ROW)
    if len(cells) != header_map.width:
        return rejected(ROW, CleaningProblem.COLUMN_COUNT_MISMATCH)
    return None


def clean_field[T](field: str, raw: str, rule: Callable[[str], FieldResult[T]]) -> FieldResult[T]:
    """SPEC_DECISIONS 82, in order: a control character anywhere in the cell as sent is UNSAFE_CHARACTERS
    (trimming is still recorded); trimming; empty; a formula lead `=`, `+`, `@` on the trimmed cell;
    then the field's own rule."""
    trimmed = trim(raw)
    trimming = (fixed(field, CleaningProblem.WHITESPACE_TRIMMED),) if trimmed != raw else ()
    if has_control_character(raw):
        return FieldResult.refused(field, CleaningProblem.UNSAFE_CHARACTERS, *trimming)
    if not trimmed:
        return FieldResult.refused(field, CleaningProblem.MISSING_REQUIRED_FIELD, *trimming)
    if trimmed[0] in UNSAFE_LEADING:
        return FieldResult.refused(field, CleaningProblem.UNSAFE_CHARACTERS, *trimming)
    result = rule(trimmed)
    return FieldResult(result.value, (*trimming, *result.issues))


def _sorted(issues: Iterable[CleaningIssue]) -> tuple[CleaningIssue, ...]:
    return tuple(sorted(issues, key=_issue_order))


def clean_row(
    cells: Sequence[str], header_map: HeaderMap, kind: TapeKind, as_of_date: date
) -> CleanedRow | RejectedRow:
    """Every issue of one raw row, and its canonical values when nothing was rejected."""
    row_issue = row_rule(cells, header_map)
    if row_issue is not None:
        return RejectedRow((row_issue,))

    def cell(field: str) -> str:
        return cells[header_map.columns[field]]

    vin = clean_field(VIN, cell(VIN), clean_vin)
    if kind is TapeKind.RELEASE:
        if vin.value is None:
            return RejectedRow(_sorted(vin.issues))
        return CleanedRow(_sorted(vin.issues), vin.value, None)
    number = clean_field(LOAN_NUMBER, cell(LOAN_NUMBER), clean_loan_number)
    borrower = clean_field(BORROWER_REF, cell(BORROWER_REF), clean_borrower_ref)
    origination = clean_field(
        ORIGINATION_DATE, cell(ORIGINATION_DATE), lambda text: clean_origination_date(text, as_of_date)
    )
    original = clean_field(
        ORIGINAL_AMOUNT, cell(ORIGINAL_AMOUNT), lambda text: clean_amount(ORIGINAL_AMOUNT, text)
    )
    balance = clean_field(
        CURRENT_BALANCE, cell(CURRENT_BALANCE), lambda text: clean_amount(CURRENT_BALANCE, text)
    )
    issues = _sorted(
        (
            *vin.issues,
            *number.issues,
            *borrower.issues,
            *origination.issues,
            *original.issues,
            *balance.issues,
            *balance_rules(original.value, balance.value),
        )
    )
    if (
        any(issue.action is CleaningAction.REJECTED for issue in issues)
        or vin.value is None
        or number.value is None
        or borrower.value is None
        or origination.value is None
        or original.value is None
        or balance.value is None
    ):
        return RejectedRow(issues)
    terms = LoanTerms(vin.value, borrower.value, origination.value, original.value)
    return CleanedRow(issues, vin.value, LoanValues(number.value, terms, balance.value))
