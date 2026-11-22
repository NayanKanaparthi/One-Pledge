"""Shape check of `examples/cleaning_cases.json`, the neutral cleaning table (SPEC_DECISIONS 50).

Part of the contract checker in the TEST DOUBLES package. It checks the form of every
case, never the expected values: those are written by hand from SPEC 10.4 and are the
reference every cleaning implementation runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

MIN_CASES = 120
FORMAT_VERSION = 1
FIELDS = frozenset(
    {
        "vin",
        "borrower_ref",
        "lender_loan_number",
        "origination_date",
        "original_amount",
        "current_balance",
        "row",
    }
)
KINDS = frozenset({"BACK_BOOK", "PLEDGE", "RELEASE"})
ACTIONS = frozenset({"ACCEPT", "FIXED", "REJECTED"})
CASE_KEYS = frozenset({"id", "field", "kind", "as_of_date", "input", "expected", "note"})
EXPECTED_KEYS = frozenset({"action", "problems", "canonical"})
FIXED_PROBLEMS = frozenset(
    {
        "WHITESPACE_TRIMMED",
        "VIN_CASE_NORMALISED",
        "VIN_SEPARATORS_REMOVED",
        "VIN_CHARACTER_SUBSTITUTED",
        "BORROWER_REF_NORMALISED",
        "DATE_FORMAT_NORMALISED",
        "AMOUNT_FORMAT_NORMALISED",
    }
)
REJECTED_PROBLEMS = frozenset(
    {
        "MISSING_REQUIRED_FIELD",
        "NON_DATA_ROW",
        "COLUMN_COUNT_MISMATCH",
        "UNSAFE_CHARACTERS",
        "VIN_LENGTH_INVALID",
        "VIN_CHARACTER_INVALID",
        "VIN_CHECK_DIGIT_INVALID",
        "BORROWER_REF_INVALID",
        "LOAN_NUMBER_INVALID",
        "DATE_UNPARSEABLE",
        "DATE_IN_FUTURE",
        "DATE_TOO_OLD",
        "AMOUNT_UNPARSEABLE",
        "AMOUNT_OUT_OF_RANGE",
        "ZERO_BALANCE",
        "BALANCE_EXCEEDS_ORIGINAL",
    }
)


def check_cleaning_cases(document: object) -> list[str]:
    """Every shape problem of the table, each naming the case id."""
    if not isinstance(document, Mapping):
        return ["cleaning_cases.json is not a JSON object"]
    problems: list[str] = []
    if document.get("format_version") != FORMAT_VERSION:
        problems.append(f"cleaning_cases.json format_version is not {FORMAT_VERSION}")
    if not isinstance(document.get("description"), str) or not document["description"]:
        problems.append("cleaning_cases.json has no description")
    cases = document.get("cases")
    if not isinstance(cases, list):
        return [*problems, "cleaning_cases.json has no cases list"]
    if len(cases) < MIN_CASES:
        problems.append(f"cleaning_cases.json holds {len(cases)} cases; at least {MIN_CASES} are required")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        identifier = case.get("id") if isinstance(case, Mapping) else None
        name = identifier if isinstance(identifier, str) else f"#{index}"
        if name in seen:
            problems.append(f"cleaning case {name} is listed twice")
        seen.add(name)
        problems.extend(f"cleaning case {name}: {problem}" for problem in _case_problems(case))
    missing = FIELDS - {case.get("field") for case in cases if isinstance(case, Mapping)}
    if missing:
        problems.append(f"cleaning_cases.json has no case for {', '.join(sorted(missing))}")
    return problems


def _case_problems(case: object) -> list[str]:
    if not isinstance(case, Mapping):
        return ["is not a JSON object"]
    if set(case) != CASE_KEYS:
        return [f"has keys {', '.join(sorted(case))} instead of {', '.join(sorted(CASE_KEYS))}"]
    problems = []
    if case["field"] not in FIELDS:
        problems.append("names an unknown field")
    if case["kind"] not in KINDS:
        problems.append("names an unknown tape kind")
    if not _is_date(case["as_of_date"]):
        problems.append("has an as_of_date that is not YYYY-MM-DD")
    if not isinstance(case["note"], str):
        problems.append("has a note that is not text")
    problems.extend(_input_problems(case["field"], case["input"]))
    problems.extend(_expected_problems(case["field"], case["expected"]))
    return problems


def _input_problems(field: object, value: object) -> list[str]:
    if field != "row":
        return [] if isinstance(value, str) else ["a field case needs a text input"]
    if not isinstance(value, Mapping) or set(value) != {"header", "cells"}:
        return ["a row case needs an input object with header and cells"]
    if not all(isinstance(v, list) and all(isinstance(c, str) for c in v) for v in value.values()):
        return ["header and cells must be lists of text"]
    return []


def _expected_problems(field: object, expected: object) -> list[str]:
    if not isinstance(expected, Mapping) or set(expected) != EXPECTED_KEYS:
        return ["expected must have exactly action, problems and canonical"]
    action, problems, canonical = expected["action"], expected["problems"], expected["canonical"]
    if action not in ACTIONS:
        return ["expected action is not ACCEPT, FIXED or REJECTED"]
    if not isinstance(problems, list) or not all(_is_problem(p) for p in problems):
        return ["expected problems must be objects of a field and a SPEC 10.4 problem"]
    codes = {p["problem"] for p in problems}
    derived = "REJECTED" if codes & REJECTED_PROBLEMS else ("FIXED" if codes else "ACCEPT")
    found = []
    if derived != action:
        found.append(f"expected action {action} does not follow from its problems ({derived})")
    if (canonical is None) != (action == "REJECTED"):
        found.append("canonical must be null exactly when the case is rejected")
    if field != "row" and any(p["field"] != field for p in problems):
        found.append("a field case lists a problem of another field")
    return found


def _is_problem(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"field", "problem"}
        and isinstance(value["field"], str)
        and value["problem"] in FIXED_PROBLEMS | REJECTED_PROBLEMS
    )


_DIGITS = frozenset("0123456789")


def _is_date(value: object) -> bool:
    """`YYYY-MM-DD` in ASCII digits, and a real date."""
    if not isinstance(value, str) or len(value) != len("YYYY-MM-DD") or value[4] + value[7] != "--":
        return False
    if not all(char in _DIGITS for index, char in enumerate(value) if index not in (4, 7)):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
