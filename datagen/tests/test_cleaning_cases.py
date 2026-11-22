"""The oracle runs every case of the neutral cleaning table (SPEC_DECISIONS 50.4).

`contracts/examples/cleaning_cases.json` was written by hand from SPEC 10.4 and decisions 13
and 45; none of the implementations produced it. Each case is asserted on its action, its
problems (as a multiset of field and problem) and its canonical value.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from onepledge_datagen.domain.oracle.cleaning import RowCleaner
from onepledge_datagen.domain.oracle.model import Action, CleanedRow, Terms
from onepledge_datagen.domain.text_values import parse_iso_date

pytestmark = pytest.mark.unit

TABLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "cleaning_cases.json"
CASES: list[dict[str, Any]] = json.loads(TABLE.read_text(encoding="utf-8"))["cases"]
# One required cell per field; the other cells are canonical and chosen so that no rule of
# another field fires (a small balance under any original amount the cases use).
HEADER = ["loan_id", "vehicle_vin", "borrower_id", "origination_date", "original_amount", "current_balance"]
FIELDS = [
    "lender_loan_number",
    "vin",
    "borrower_ref",
    "origination_date",
    "original_amount",
    "current_balance",
]
BASE = ["SAF-1", "1HGCM82633A004352", "C0048213", "2026-08-14", "23450.17", "0.01"]
TERM_NAMES = {
    "lender_loan_number": "lender_loan_number",
    "vin": "vin",
    "borrower_ref": "borrower_ref",
    "origination_date": "origination_date",
    "original_amount": "original_amount_cents",
    "current_balance": "current_balance_cents",
}


def run_case(case: dict[str, Any]) -> CleanedRow:
    as_of = parse_iso_date(case["as_of_date"])
    assert as_of is not None
    release = case["kind"] == "RELEASE"
    if case["field"] == "row":
        header, cells = case["input"]["header"], case["input"]["cells"]
    elif release:
        header, cells = HEADER, ["", case["input"], "", "", "", ""]
    else:
        header, cells = HEADER, list(BASE)
        cells[FIELDS.index(case["field"])] = case["input"]
        if case["field"] == "current_balance":
            cells[FIELDS.index("original_amount")] = "250000.00"
    return RowCleaner(header, release, as_of).clean(cells)


def action_of(row: CleanedRow) -> str:
    if row.rejected:
        return "REJECTED"
    return "FIXED" if any(issue.action == Action.FIXED for issue in row.issues) else "ACCEPT"


def canonical_of(case: dict[str, Any], row: CleanedRow) -> object:
    if row.rejected:
        return None
    if case["kind"] == "RELEASE":
        return row.vin if case["field"] != "row" else {"vin": row.vin}
    terms = row.terms
    assert isinstance(terms, Terms)
    if case["field"] == "row":
        return {field: getattr(terms, name) for field, name in TERM_NAMES.items()}
    return getattr(terms, TERM_NAMES[case["field"]])


def test_the_table_is_the_size_decision_50_asks_for() -> None:
    assert len(CASES) >= 120
    assert {case["field"] for case in CASES} == {*FIELDS, "row"}


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_the_oracle_agrees_with_every_case(case: dict[str, Any]) -> None:
    row = run_case(case)
    expected = case["expected"]
    problems = Counter((issue.field, issue.problem.value) for issue in row.issues)
    assert problems == Counter((p["field"], p["problem"]) for p in expected["problems"]), case["note"]
    assert action_of(row) == expected["action"]
    assert canonical_of(case, row) == expected["canonical"]
