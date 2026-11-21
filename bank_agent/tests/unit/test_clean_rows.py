"""`clean_row`: every rule of SPEC 10.4 in the order of SPEC_DECISIONS 13, for both lender formats."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bank_testkit.tapes import LAKESIDE_HEADER, SUMMIT_HEADER, lakeside_row, summit_row
from onepledge_bank_agent.domain.cleaning import CleanedRow, HeaderMap, RejectedRow, clean_row
from onepledge_bank_agent.domain.enums import CleaningAction, CleaningProblem, TapeKind
from onepledge_bank_agent.domain.tape_file import raw_payload

pytestmark = pytest.mark.unit

AS_OF = date(2026, 9, 30)
SUMMIT = HeaderMap.from_header(SUMMIT_HEADER, TapeKind.PLEDGE)
LAKESIDE = HeaderMap.from_header(LAKESIDE_HEADER, TapeKind.PLEDGE)
P = CleaningProblem


def summit(**cells: str) -> Mapping[str, str]:
    return raw_payload(SUMMIT_HEADER, summit_row(**cells))


def issues(result: CleanedRow | RejectedRow) -> set[tuple[str, CleaningProblem, CleaningAction]]:
    return {(i.field, i.problem, i.action) for i in result.issues}


def fixed(field: str, problem: CleaningProblem) -> tuple[str, CleaningProblem, CleaningAction]:
    return field, problem, CleaningAction.FIXED


def rejected(field: str, problem: CleaningProblem) -> tuple[str, CleaningProblem, CleaningAction]:
    return field, problem, CleaningAction.REJECTED


def clean(
    payload: Mapping[str, str], header: HeaderMap = SUMMIT, kind: TapeKind = TapeKind.PLEDGE
) -> CleanedRow | RejectedRow:
    return clean_row(payload, header, kind, AS_OF)


def accepted(payload: Mapping[str, str], header: HeaderMap = SUMMIT) -> CleanedRow:
    result = clean(payload, header)
    assert isinstance(result, CleanedRow), result.issues
    return result


def test_a_clean_summit_row_is_accepted_with_canonical_values_and_no_issue() -> None:
    result = accepted(summit())
    assert result.issues == ()
    assert result.loan is not None
    terms = result.loan.terms
    assert (terms.vin.value, terms.borrower_ref.value, terms.origination_date) == (
        "1HGCM82633A004352",
        "C1234567",
        date(2025, 3, 15),
    )
    assert (terms.original_amount.cents, result.loan.current_balance.cents) == (2345017, 1800000)
    assert result.loan.lender_loan_number.value == "SAF-1234567-01"


def test_a_lakeside_row_carries_only_its_inherent_borrower_fix() -> None:
    result = accepted(raw_payload(LAKESIDE_HEADER, lakeside_row()), LAKESIDE)
    assert issues(result) == {fixed("borrower_ref", P.BORROWER_REF_NORMALISED)}
    assert result.loan is not None and result.loan.terms.borrower_ref.value == "LK123456"


@pytest.mark.parametrize("field", ["loan_number", "vin", "borrower", "origination", "original", "balance"])
def test_an_empty_required_cell_is_missing_and_nothing_else(field: str) -> None:
    result = clean(summit(**{field: ""}))
    assert isinstance(result, RejectedRow)
    assert len(result.issues) == 1 and result.issues[0].problem is P.MISSING_REQUIRED_FIELD


def test_trimming_a_required_cell_is_recorded_once_per_field() -> None:
    result = accepted(summit(vin=" 1HGCM82633A004352 ", borrower="C1234567  "))
    assert issues(result) == {fixed("vin", P.WHITESPACE_TRIMMED), fixed("borrower_ref", P.WHITESPACE_TRIMMED)}


def test_a_blank_after_trimming_keeps_the_trim_and_is_missing() -> None:
    assert issues(clean(summit(vin="   "))) == {
        fixed("vin", P.WHITESPACE_TRIMMED),
        rejected("vin", P.MISSING_REQUIRED_FIELD),
    }


def test_a_row_of_empty_cells_is_a_non_data_row() -> None:
    payload = dict.fromkeys(SUMMIT_HEADER, " ")
    assert issues(clean(payload)) == {rejected("row", P.NON_DATA_ROW)}


@pytest.mark.parametrize("marker", ["TOTAL", "Totals", "grand total", "SubTotal", " TOTAL "])
def test_a_totals_row_is_a_non_data_row_even_when_short(marker: str) -> None:
    full = raw_payload(SUMMIT_HEADER, [marker, "", "", "", "", "$1.00", "", "", "", "", "", ""])
    short = raw_payload(SUMMIT_HEADER, [marker, "", "", "", "", "$1.00"])
    assert issues(clean(full)) == {rejected("row", P.NON_DATA_ROW)}
    assert issues(clean(short)) == {rejected("row", P.NON_DATA_ROW)}


@pytest.mark.parametrize("cells", [summit_row()[:-1], [*summit_row(), "extra"]])
def test_a_row_with_another_cell_count_is_a_column_count_mismatch(cells: list[str]) -> None:
    assert issues(clean(raw_payload(SUMMIT_HEADER, cells))) == {rejected("row", P.COLUMN_COUNT_MISMATCH)}


@pytest.mark.parametrize("cell", ["=1+1", "+12", "@SUM(1+1)", "\tSAF-1", " =1", "SAF\x01-1", "SAF-1\r"])
def test_a_formula_or_control_character_is_unsafe_and_nothing_else(cell: str) -> None:
    result = clean(summit(loan_number=cell))
    assert {i.problem for i in result.issues} <= {P.UNSAFE_CHARACTERS, P.WHITESPACE_TRIMMED}
    assert rejected("lender_loan_number", P.UNSAFE_CHARACTERS) in issues(result)


def test_an_unsafe_borrower_is_rejected() -> None:
    assert issues(clean(summit(borrower="@SUM(1+1)"))) == {rejected("borrower_ref", P.UNSAFE_CHARACTERS)}


def test_a_loan_number_with_a_leading_minus_is_a_pattern_failure() -> None:
    assert issues(clean(summit(loan_number="-SAF1"))) == {
        rejected("lender_loan_number", P.LOAN_NUMBER_INVALID)
    }


def test_a_loan_number_with_a_space_is_invalid() -> None:
    assert issues(clean(summit(loan_number="SAF 1"))) == {
        rejected("lender_loan_number", P.LOAN_NUMBER_INVALID)
    }


def test_a_lower_case_vin_is_fixed() -> None:
    result = accepted(summit(vin="1hgcm82633a004352"))
    assert issues(result) == {fixed("vin", P.VIN_CASE_NORMALISED)} and result.vin.value == "1HGCM82633A004352"


@pytest.mark.parametrize("vin", ["1HG-CM826-33A004352", "1HG CM82633 A004352", "1HGCM-82633-A004352"])
def test_separators_in_a_vin_are_removed(vin: str) -> None:
    result = accepted(summit(vin=vin))
    assert issues(result) == {fixed("vin", P.VIN_SEPARATORS_REMOVED)}


def test_case_and_separators_are_both_recorded() -> None:
    result = accepted(summit(vin="1hgcm-82633-a004352"))
    assert issues(result) == {fixed("vin", P.VIN_CASE_NORMALISED), fixed("vin", P.VIN_SEPARATORS_REMOVED)}


@pytest.mark.parametrize("vin", ["1HGCM82633AOO4352", "1HGCM82633A0Q4352", "IHGCM82633A004352"])
def test_look_alike_letters_are_substituted_when_that_makes_a_valid_vin(vin: str) -> None:
    result = accepted(summit(vin=vin))
    assert (
        issues(result) == {fixed("vin", P.VIN_CHARACTER_SUBSTITUTED)}
        and result.vin.value == "1HGCM82633A004352"
    )


def test_a_look_alike_that_does_not_validate_is_an_invalid_character() -> None:
    assert issues(clean(summit(vin="1HGCM82633A00435O"))) == {rejected("vin", P.VIN_CHARACTER_INVALID)}


@pytest.mark.parametrize("vin", ["1HGCM82633A00435*", "1HGCM82633A00435Ä"])
def test_a_character_outside_the_alphabet_is_invalid(vin: str) -> None:
    assert issues(clean(summit(vin=vin))) == {rejected("vin", P.VIN_CHARACTER_INVALID)}


@pytest.mark.parametrize("vin", ["1HGCM82633A00435", "1HGCM82633A0043521", "1HG-CM82633A00435"])
def test_a_vin_of_the_wrong_length_is_rejected(vin: str) -> None:
    assert rejected("vin", P.VIN_LENGTH_INVALID) in issues(clean(summit(vin=vin)))


@pytest.mark.parametrize("vin", ["1HGCM82633A004353", "1HGMC82633A004352"])
def test_a_wrong_check_digit_is_rejected(vin: str) -> None:
    assert issues(clean(summit(vin=vin))) == {rejected("vin", P.VIN_CHECK_DIGIT_INVALID)}


def test_a_fixed_issue_is_kept_when_the_field_is_rejected_later() -> None:
    assert issues(clean(summit(vin="1hgcm82633a004353"))) == {
        fixed("vin", P.VIN_CASE_NORMALISED),
        rejected("vin", P.VIN_CHECK_DIGIT_INVALID),
    }


def test_a_lower_case_borrower_is_normalised() -> None:
    result = accepted(summit(borrower="c1234567"))
    assert issues(result) == {fixed("borrower_ref", P.BORROWER_REF_NORMALISED)}


def test_a_borrower_that_is_too_short_after_normalising_is_invalid() -> None:
    assert issues(clean(summit(borrower="c-12"))) == {
        fixed("borrower_ref", P.BORROWER_REF_NORMALISED),
        rejected("borrower_ref", P.BORROWER_REF_INVALID),
    }


@pytest.mark.parametrize(
    ("text", "day"),
    [("2025-03-15", date(2025, 3, 15)), ("03/15/2025", date(2025, 3, 15)), ("2026-09-30", AS_OF)],
)
def test_the_two_accepted_date_formats(text: str, day: date) -> None:
    result = accepted(summit(origination=text))
    assert result.issues == () and result.loan is not None and result.loan.terms.origination_date == day


@pytest.mark.parametrize("text", ["14-Aug-2026", "14-aug-2026", "14-AUG-2026"])
def test_day_month_year_is_fixed_in_any_case(text: str) -> None:
    result = accepted(summit(origination=text))
    assert issues(result) == {fixed("origination_date", P.DATE_FORMAT_NORMALISED)}
    assert result.loan is not None and result.loan.terms.origination_date == date(2026, 8, 14)


@pytest.mark.parametrize(
    "text", ["13/45/2026", "02/30/2025", "2025-3-15", "15.03.2025", "14-Foo-2026", "31-Feb-2026", "yesterday"]
)
def test_an_unreadable_date_is_unparseable(text: str) -> None:
    assert issues(clean(summit(origination=text))) == {rejected("origination_date", P.DATE_UNPARSEABLE)}


def test_a_date_after_the_as_of_date_is_in_the_future() -> None:
    assert issues(clean(summit(origination="10/01/2026"))) == {rejected("origination_date", P.DATE_IN_FUTURE)}


def test_a_date_before_2000_is_too_old_and_a_fixed_format_is_kept() -> None:
    assert issues(clean(summit(origination="1999-12-31"))) == {rejected("origination_date", P.DATE_TOO_OLD)}
    assert issues(clean(summit(origination="31-Dec-1999"))) == {
        fixed("origination_date", P.DATE_FORMAT_NORMALISED),
        rejected("origination_date", P.DATE_TOO_OLD),
    }


@pytest.mark.parametrize(
    ("text", "cents"),
    [("23450.17", 2345017), ("$23,450.17", 2345017), ("23,450.17", 2345017), ("$123,456.00", 12345600)],
)
def test_amounts_accepted_without_an_issue(text: str, cents: int) -> None:
    result = accepted(summit(original=text, balance="100.00"))
    assert (
        result.issues == () and result.loan is not None and result.loan.terms.original_amount.cents == cents
    )


@pytest.mark.parametrize(
    ("text", "cents"),
    [("23450", 2345000), ("23450.5", 2345050), ("$23,450", 2345000), ("$23,450.5", 2345050)],
)
def test_amounts_without_two_decimals_are_fixed(text: str, cents: int) -> None:
    result = accepted(summit(original=text, balance="100.00"))
    assert issues(result) == {fixed("original_amount", P.AMOUNT_FORMAT_NORMALISED)}
    assert result.loan is not None and result.loan.terms.original_amount.cents == cents


@pytest.mark.parametrize(
    "text",
    ["N/A", "23,45.00", "2,3450.00", "23450.175", "(23450.00)", "$-23450.00", "23450.17 USD", "1e5", "$"],
)
def test_anything_else_is_unparseable(text: str) -> None:
    assert issues(clean(summit(original=text))) == {rejected("original_amount", P.AMOUNT_UNPARSEABLE)}


def test_a_leading_minus_is_out_of_range_and_keeps_a_format_fix() -> None:
    assert issues(clean(summit(original="-23450.00"))) == {rejected("original_amount", P.AMOUNT_OUT_OF_RANGE)}
    assert issues(clean(summit(balance="-100"))) == {
        fixed("current_balance", P.AMOUNT_FORMAT_NORMALISED),
        rejected("current_balance", P.AMOUNT_OUT_OF_RANGE),
    }


@pytest.mark.parametrize(
    ("original", "ok"),
    [
        ("99.99", False),
        ("100.00", True),
        ("250,000.00", True),
        ("250,000.01", False),
        ("999999999999999.00", False),
    ],
)
def test_the_original_amount_range(original: str, ok: bool) -> None:
    result = clean(summit(original=original, balance="50.00"))
    assert isinstance(result, CleanedRow) is ok
    if not ok:
        assert issues(result) == {rejected("original_amount", P.AMOUNT_OUT_OF_RANGE)}


def test_a_zero_balance_is_rejected() -> None:
    assert issues(clean(summit(balance="0.00"))) == {rejected("current_balance", P.ZERO_BALANCE)}


def test_a_balance_above_the_original_is_rejected() -> None:
    assert issues(clean(summit(original="1000.00", balance="1000.01"))) == {
        rejected("current_balance", P.BALANCE_EXCEEDS_ORIGINAL)
    }


def test_every_field_is_evaluated_so_the_review_list_shows_everything() -> None:
    result = clean(
        summit(
            vin="1HGCM82633A004353", borrower="x", origination="13/45/2026", original="N/A", loan_number="=1"
        )
    )
    assert {i.problem for i in result.issues} == {
        P.VIN_CHECK_DIGIT_INVALID,
        P.BORROWER_REF_INVALID,
        P.BORROWER_REF_NORMALISED,
        P.DATE_UNPARSEABLE,
        P.AMOUNT_UNPARSEABLE,
        P.UNSAFE_CHARACTERS,
    }


def test_a_release_row_needs_only_a_valid_vin() -> None:
    header = HeaderMap.from_header(SUMMIT_HEADER, TapeKind.RELEASE)
    row = raw_payload(SUMMIT_HEADER, ["SAF-1", "1hgcm82633a004352", "", "", "", "", "", "", "", "", "", ""])
    result = clean(row, header, TapeKind.RELEASE)
    assert isinstance(result, CleanedRow) and result.loan is None
    assert issues(result) == {fixed("vin", P.VIN_CASE_NORMALISED)}
    bad = raw_payload(SUMMIT_HEADER, ["SAF-1", "1HGCM82633A004353", "", "", "", "", "", "", "", "", "", ""])
    assert issues(clean(bad, header, TapeKind.RELEASE)) == {rejected("vin", P.VIN_CHECK_DIGIT_INVALID)}


def test_a_header_rebuilt_from_stored_keys_maps_the_same_columns() -> None:
    rebuilt = HeaderMap.from_stored_keys([*summit().keys(), "__extra_1"])
    assert rebuilt.columns == SUMMIT.columns and rebuilt.names == SUMMIT.names
    assert rebuilt.lead_field == "lender_loan_number"
    assert HeaderMap.from_stored_keys(["VIN"]).lead_field == "vin"


def test_a_stored_tape_without_a_required_column_rejects_every_row_as_a_count_mismatch() -> None:
    short = HeaderMap.from_stored_keys(name for name in SUMMIT_HEADER if name != "Curr Bal")
    payload = {name: cell for name, cell in summit().items() if name in short.names}
    assert issues(clean(payload, short)) == {rejected("row", P.COLUMN_COUNT_MISMATCH)}


VIN_CELLS = st.sampled_from(
    ["1HGCM82633A004352", "1hgcm82633a004352", "1HG-CM826-33A004352", "1HGCM82633AOO4352"]
)
DATE_CELLS = st.dates(date(2000, 1, 1), AS_OF).flatmap(
    lambda d: st.sampled_from([d.isoformat(), d.strftime("%m/%d/%Y"), d.strftime("%d-%b-%Y")])
)
CENTS = st.integers(min_value=10_000, max_value=25_000_000)


def _amount_text(cents: int, style: int) -> str:
    dollars, rest = divmod(cents, 100)
    return [f"{dollars}.{rest:02d}", f"${dollars:,}.{rest:02d}", f"{dollars}"][style]


ROWS = st.tuples(
    VIN_CELLS,
    st.text(alphabet="ABCabc0123456789-", min_size=4, max_size=12),
    DATE_CELLS,
    CENTS,
    st.integers(0, 2),
    st.integers(0, 2),
)


@given(ROWS)
def test_cleaning_is_idempotent_on_its_own_output(row: tuple[str, str, str, int, int, int]) -> None:
    vin, borrower, origination, cents, style, balance_style = row
    original = _amount_text(cents, style)
    balance = _amount_text(max(cents // 3 // 100 * 100, 100), balance_style)
    first = clean(
        summit(vin=vin, borrower=borrower, origination=origination, original=original, balance=balance)
    )
    if isinstance(first, RejectedRow):
        return
    assert first.loan is not None
    canonical = summit(
        vin=first.vin.value,
        borrower=first.loan.terms.borrower_ref.value,
        origination=first.loan.terms.origination_date.isoformat(),
        original=_amount_text(first.loan.terms.original_amount.cents, 0),
        balance=_amount_text(first.loan.current_balance.cents, 0),
        loan_number=first.loan.lender_loan_number.value,
    )
    second = accepted(canonical)
    assert second.issues == ()
    assert second.loan == first.loan and second.vin == first.vin
