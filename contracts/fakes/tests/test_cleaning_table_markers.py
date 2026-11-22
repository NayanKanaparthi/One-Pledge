"""The mechanical markers of the neutral cleaning table agree with SPEC 10.4 (SPEC_DECISIONS 68.12, 82, 83).

Some expected problems follow from the input by rules too simple to need an implementation:
trimming, an empty cell, an unsafe cell, ASCII lower case or separators in a VIN, and a
borrower reference that is not already canonical. This test derives those markers from each
input by hand rules written here, and requires every field case to list exactly them. It is
the check that would have caught the transcription error corrected in vin-021 (041cedb).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.contract

TABLE = Path(__file__).resolve().parents[2] / "examples" / "cleaning_cases.json"
CASES: list[dict[str, Any]] = json.loads(TABLE.read_text(encoding="utf-8"))["cases"]
ASCII_WHITESPACE = " \t\r\n"
ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"
FORMULA_LEAD = "=+@"
MARKERS = frozenset(
    {
        "WHITESPACE_TRIMMED",
        "MISSING_REQUIRED_FIELD",
        "UNSAFE_CHARACTERS",
        "VIN_CASE_NORMALISED",
        "VIN_SEPARATORS_REMOVED",
        "BORROWER_REF_NORMALISED",
    }
)
STOPS = frozenset({"MISSING_REQUIRED_FIELD", "UNSAFE_CHARACTERS"})


def field_rule_markers(field: str, text: str) -> set[str]:
    """The FIXED markers of the field's own rules that the input alone decides."""
    if field == "vin":
        markers = {"VIN_CASE_NORMALISED"} if any(char in ASCII_LOWER for char in text) else set()
        return markers | ({"VIN_SEPARATORS_REMOVED"} if any(char in " -" for char in text) else set())
    if field == "borrower_ref":
        canonical = "".join(
            char.upper() if char in ASCII_LOWER else char for char in text if char not in " -_./"
        )
        return {"BORROWER_REF_NORMALISED"} if canonical != text else set()
    return set()


def is_control(char: str) -> bool:
    """U+0000 to U+001F or U+007F (decision 82): tab, CR and LF included."""
    return ord(char) < 0x20 or ord(char) == 0x7F


def expected_markers(field: str, raw: str) -> set[str]:
    """The markers SPEC 10.4 and decision 82 give a cell, in their order.

    Control characters on the cell as sent (trimming still recorded), trim, empty, formula
    lead on the trimmed cell, then the field's own rules.
    """
    text = raw.strip(ASCII_WHITESPACE)
    markers = {"WHITESPACE_TRIMMED"} if text != raw else set()
    if any(is_control(char) for char in raw):
        return markers | {"UNSAFE_CHARACTERS"}
    if not text:
        return markers | {"MISSING_REQUIRED_FIELD"}
    if text[0] in FORMULA_LEAD:
        return markers | {"UNSAFE_CHARACTERS"}
    return markers | field_rule_markers(field, text)


FIELD_CASES = [case for case in CASES if case["field"] != "row"]


@pytest.mark.parametrize("case", FIELD_CASES, ids=[case["id"] for case in FIELD_CASES])
def test_every_field_case_lists_exactly_its_mechanical_markers(case: dict[str, Any]) -> None:
    problems = [problem["problem"] for problem in case["expected"]["problems"]]
    expected = expected_markers(case["field"], case["input"])
    assert set(problems) & MARKERS == expected, case["note"]
    if expected & STOPS:
        assert set(problems) <= expected, "nothing may follow an empty or unsafe cell"


def test_the_marker_rules_reject_the_original_vin_021() -> None:
    """The entry before 041cedb: ASCII lower case letters with a Cyrillic small a, listing no case fix."""
    assert expected_markers("vin", "1hgcm82633\u0430004352") == {"VIN_CASE_NORMALISED"}
    original_problems = {"VIN_CHARACTER_INVALID"}
    assert original_problems & MARKERS != expected_markers("vin", "1hgcm82633\u0430004352")


def test_the_table_holds_the_cases_of_decision_84() -> None:
    by_input = {(case["field"], case["input"]): case for case in FIELD_CASES}
    for raw in ("LK-#123456", "lk#123456", "LK 12\u06634", "c004821\u0663", "LK-12\u00e94"):
        problems = {p["problem"] for p in by_input[("borrower_ref", raw)]["expected"]["problems"]}
        assert problems == {"BORROWER_REF_NORMALISED", "BORROWER_REF_INVALID"}, raw
    assert {p["problem"] for p in by_input[("borrower_ref", "#123456")]["expected"]["problems"]} == {
        "BORROWER_REF_INVALID"
    }
    for field, value in (("vin", "1HGCM82633A004352"), ("borrower_ref", "C0048213"),
                         ("original_amount", "23450.17"), ("origination_date", "2026-08-14")):  # fmt: skip
        for tail in ("\t", "\r", "\n"):
            assert by_input[(field, value + tail)]["expected"]["action"] == "REJECTED"
        accepted = [
            c
            for c in FIELD_CASES
            if c["field"] == field and c["input"].rstrip(" ") == value and c["input"] != value
        ]
        assert any(c["expected"]["action"] == "FIXED" for c in accepted), field
    assert by_input[("lender_loan_number", " SAF-1 ")]["expected"]["canonical"] == "SAF-1"
