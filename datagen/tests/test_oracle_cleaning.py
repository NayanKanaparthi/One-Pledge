"""The oracle's cleaning rules: one case per row of the SPEC 10.4 table, in both lender formats."""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from onepledge_datagen.domain.oracle.cleaning import RowCleaner, TapeFormatError, map_header, parse_tape
from onepledge_datagen.domain.oracle.model import Action, Problem
from onepledge_datagen.domain.tape_format import LAKESIDE_FORMAT, SUMMIT_FORMAT, LenderFormat

pytestmark = pytest.mark.unit

AS_OF = date(2026, 9, 30)
SUMMIT_ROW = ["SAF-1234567-01", "1HGCM82633A004352", "C1234567", "08/14/2026", "$23,450.17", "$20,000.00"]
LAKESIDE_ROW = ["LMC12345678", "1HGCM82633A004352", "LK123456", "2026-08-14", "23450.17", "20000.00"]
EXTRA = ["18.9", "60", "2021", "Honda", "Civic", "NJ"]
FORMATS = [(SUMMIT_FORMAT, SUMMIT_ROW), (LAKESIDE_FORMAT, LAKESIDE_ROW)]


def clean(fmt: LenderFormat, base: list[str], changes: dict[int, str]) -> set[tuple[str, str, str]]:
    row = [*base, *EXTRA]
    for column, value in changes.items():
        row[column] = value
    result = RowCleaner(fmt.header, False, AS_OF).clean(row)
    return {(i.field, i.problem.value, i.action.value) for i in result.issues}


@pytest.mark.parametrize(("fmt", "base"), FORMATS)
def test_a_canonical_row_has_no_issue(fmt: LenderFormat, base: list[str]) -> None:
    assert clean(fmt, base, {}) == set()


@pytest.mark.parametrize(("fmt", "base"), FORMATS)
@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [
        (1, "", ("vin", "MISSING_REQUIRED_FIELD", "REJECTED")),
        (1, " 1HGCM82633A004352 ", ("vin", "WHITESPACE_TRIMMED", "FIXED")),
        (0, "=1+1", ("lender_loan_number", "UNSAFE_CHARACTERS", "REJECTED")),
        (2, "@SUM(1+1)", ("borrower_ref", "UNSAFE_CHARACTERS", "REJECTED")),
        (2, "C123\x074567", ("borrower_ref", "UNSAFE_CHARACTERS", "REJECTED")),
        (1, "1hgcm82633a004352", ("vin", "VIN_CASE_NORMALISED", "FIXED")),
        (1, "1HG-CM826 33A004352", ("vin", "VIN_SEPARATORS_REMOVED", "FIXED")),
        (1, "1HGCM82633AOO4352", ("vin", "VIN_CHARACTER_SUBSTITUTED", "FIXED")),
        (1, "1HGCM82633A00435", ("vin", "VIN_LENGTH_INVALID", "REJECTED")),
        (1, "1HGCM82633A00435*", ("vin", "VIN_CHARACTER_INVALID", "REJECTED")),
        (1, "1HGCM82633A004353", ("vin", "VIN_CHECK_DIGIT_INVALID", "REJECTED")),
        (2, "c1234567", ("borrower_ref", "BORROWER_REF_NORMALISED", "FIXED")),
        (2, "C-1", ("borrower_ref", "BORROWER_REF_INVALID", "REJECTED")),
        (0, "SAF 123", ("lender_loan_number", "LOAN_NUMBER_INVALID", "REJECTED")),
        (3, "14-Aug-2026", ("origination_date", "DATE_FORMAT_NORMALISED", "FIXED")),
        (3, "13/45/2026", ("origination_date", "DATE_UNPARSEABLE", "REJECTED")),
        (3, "2026-02-30", ("origination_date", "DATE_UNPARSEABLE", "REJECTED")),
        (3, "12/31/2027", ("origination_date", "DATE_IN_FUTURE", "REJECTED")),
        (3, "1999-12-31", ("origination_date", "DATE_TOO_OLD", "REJECTED")),
        (4, "23450", ("original_amount", "AMOUNT_FORMAT_NORMALISED", "FIXED")),
        (4, "23450.5", ("original_amount", "AMOUNT_FORMAT_NORMALISED", "FIXED")),
        (4, "$23,450", ("original_amount", "AMOUNT_FORMAT_NORMALISED", "FIXED")),
        (4, "N/A", ("original_amount", "AMOUNT_UNPARSEABLE", "REJECTED")),
        (4, "23450.171", ("original_amount", "AMOUNT_UNPARSEABLE", "REJECTED")),
        (4, "(23450.17)", ("original_amount", "AMOUNT_UNPARSEABLE", "REJECTED")),
        (4, "-23450.00", ("original_amount", "AMOUNT_OUT_OF_RANGE", "REJECTED")),
        (4, "99.99", ("original_amount", "AMOUNT_OUT_OF_RANGE", "REJECTED")),
        (4, "250000.01", ("original_amount", "AMOUNT_OUT_OF_RANGE", "REJECTED")),
        (5, "0.00", ("current_balance", "ZERO_BALANCE", "REJECTED")),
        (5, "23450.18", ("current_balance", "BALANCE_EXCEEDS_ORIGINAL", "REJECTED")),
    ],
)
def test_each_rule_of_the_table(
    fmt: LenderFormat, base: list[str], column: int, value: str, expected: tuple[str, str, str]
) -> None:
    issues = clean(fmt, base, {column: value})
    assert expected in issues
    if expected[2] == "FIXED" and not (fmt is LAKESIDE_FORMAT and column == 2):
        assert issues == {expected}


@pytest.mark.parametrize("first_cell", ["TOTAL", "totals", "Grand Total", "SUBTOTAL"])
def test_totals_rows_are_not_data(first_cell: str) -> None:
    row = [first_cell, "", "", "", "", "$1.00", "", "", "", "", "", ""]
    issues = RowCleaner(SUMMIT_FORMAT.header, False, AS_OF).clean(row).issues
    assert [(i.problem, i.action) for i in issues] == [(Problem.NON_DATA_ROW, Action.REJECTED)]


def test_empty_rows_and_wrong_widths() -> None:
    cleaner = RowCleaner(SUMMIT_FORMAT.header, False, AS_OF)
    assert cleaner.clean([""] * 12).issues[0].problem == Problem.NON_DATA_ROW
    assert cleaner.clean([*SUMMIT_ROW, *EXTRA, "x"]).issues[0].problem == Problem.COLUMN_COUNT_MISMATCH


def test_release_rows_use_only_the_row_and_vin_rules() -> None:
    cleaner = RowCleaner(SUMMIT_FORMAT.header, True, AS_OF)
    row = ["SAF-1", "1hgcm82633a004352", "", "", "", "", "", "", "", "", "", ""]
    result = cleaner.clean(row)
    assert not result.rejected and result.vin == "1HGCM82633A004352" and result.terms is None


def test_lakeside_borrower_references_always_lose_their_hyphen() -> None:
    assert clean(LAKESIDE_FORMAT, LAKESIDE_ROW, {2: "LK-123456"}) == {
        ("borrower_ref", "BORROWER_REF_NORMALISED", "FIXED")
    }


def test_every_field_is_evaluated_with_no_stop_at_the_first_problem() -> None:
    issues = clean(SUMMIT_FORMAT, SUMMIT_ROW, {1: "", 3: "13/45/2026", 4: "N/A"})
    assert {problem for _, problem, _ in issues} == {
        "MISSING_REQUIRED_FIELD",
        "DATE_UNPARSEABLE",
        "AMOUNT_UNPARSEABLE",
    }


@pytest.mark.parametrize(
    "header",
    [
        ("Loan #", "VIN", "Cust ID", "Orig Date", "Orig Amt"),
        ("Loan #", "VIN", "vin", "Cust ID", "Orig Date", "Orig Amt", "Curr Bal"),
        ("Loan #", "VIN", "vehicle_vin", "Cust ID", "Orig Date", "Orig Amt", "Curr Bal"),
        ("Loan #", "", "Cust ID", "Orig Date", "Orig Amt", "Curr Bal"),
    ],
)
def test_bad_headers_are_refused(header: tuple[str, ...]) -> None:
    with pytest.raises(TapeFormatError):
        map_header(
            header,
            (
                "lender_loan_number",
                "vin",
                "borrower_ref",
                "origination_date",
                "original_amount",
                "current_balance",
            ),
        )


def test_header_names_are_matched_loosely() -> None:
    mapping = map_header(("  LOAN   #", "Vin", "customer id"), ("vin",))
    assert mapping == {"lender_loan_number": 0, "vin": 1, "borrower_ref": 2}


def test_tape_parsing() -> None:
    parsed = parse_tape("﻿a,b\r\n1,2\r\n\r\n3,4\r\n".encode())
    assert parsed.header == ("a", "b") and parsed.records == (("1", "2"), ("3", "4"))
    for bad in (b"\xff\xfe", b"", b'a,b\n"open,2\n'):
        with pytest.raises(TapeFormatError):
            parse_tape(bad)


summit_rows = st.fixed_dictionaries(
    {
        1: st.sampled_from(
            ["1hgcm82633a004352", "1HG-CM82633A004352", "1HGCM82633AOO4352", " 1HGCM82633A004352 "]
        ),
        2: st.sampled_from(["c1234567", "C-1234567", "C1234567"]),
        3: st.sampled_from(["14-Aug-2026", "08/14/2026", "2026-08-14"]),
        4: st.sampled_from(["23450", "$23,450", "23450.5", "$23,450.00"]),
    }
)


@given(summit_rows)
def test_property_cleaning_is_idempotent_on_its_own_output(changes: dict[int, str]) -> None:
    row = [*SUMMIT_ROW, *EXTRA]
    for column, value in changes.items():
        row[column] = value
    first = RowCleaner(SUMMIT_FORMAT.header, False, AS_OF).clean(row)
    assert first.terms is not None and not first.rejected
    canonical = [
        first.terms.lender_loan_number,
        first.terms.vin,
        first.terms.borrower_ref,
        first.terms.origination_date,
        f"{first.terms.original_amount_cents // 100}.{first.terms.original_amount_cents % 100:02d}",
        f"{first.terms.current_balance_cents // 100}.{first.terms.current_balance_cents % 100:02d}",
        *EXTRA,
    ]
    second = RowCleaner(SUMMIT_FORMAT.header, False, AS_OF).clean(canonical)
    assert second.issues == () and second.terms == first.terms


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("\t", {("vin", "UNSAFE_CHARACTERS", "REJECTED"), ("vin", "WHITESPACE_TRIMMED", "FIXED")}),
        ("  ", {("vin", "MISSING_REQUIRED_FIELD", "REJECTED"), ("vin", "WHITESPACE_TRIMMED", "FIXED")}),
        ("\x1f1HGCM82633A004352", {("vin", "UNSAFE_CHARACTERS", "REJECTED")}),
        ("1HGCM82633A004352\x7f", {("vin", "UNSAFE_CHARACTERS", "REJECTED")}),
        ("+1HGCM82633A004352", {("vin", "UNSAFE_CHARACTERS", "REJECTED")}),
        ("1HGCM82633A00435\x9f", {("vin", "VIN_CHARACTER_INVALID", "REJECTED")}),
    ],
)
def test_the_cell_steps_follow_decision_82(cell: str, expected: set[tuple[str, str, str]]) -> None:
    """Control characters on the cell as sent, then trimming, empty, formula lead, field rules."""
    assert clean(LAKESIDE_FORMAT, LAKESIDE_ROW, {1: cell}) == expected
