"""The mechanical markers of the neutral cleaning table agree with SPEC 10.4 (SPEC_DECISIONS 68.12).

Some expected problems follow from the input by rules too simple to need an implementation:
trimming, an empty cell, an unsafe cell, ASCII lower case or separators in a VIN, and a
borrower reference that is not already canonical. This test derives those markers from each
input by hand rules written here, and requires every field case to list exactly them. It is
the check that would have caught the transcription error corrected in vin-021 (041cedb).
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.contract

TABLE = Path(__file__).resolve().parents[2] / "examples" / "cleaning_cases.json"
CASES: list[dict[str, Any]] = json.loads(TABLE.read_text(encoding="utf-8"))["cases"]
ASCII_WHITESPACE = " \t\r\n"
ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"
UNSAFE_FIRST = "=+@\t\r"
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


def expected_markers(field: str, raw: str) -> set[str]:
    """The markers SPEC 10.4 and decision 13 give a cell, in their order: trim, empty, unsafe, field."""
    text = raw.strip(ASCII_WHITESPACE)
    markers = {"WHITESPACE_TRIMMED"} if text != raw else set()
    if not text:
        return markers | {"MISSING_REQUIRED_FIELD"}
    unsafe = raw[0] in UNSAFE_FIRST or text[0] in UNSAFE_FIRST
    if unsafe or any(unicodedata.category(char) == "Cc" for char in raw):
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
