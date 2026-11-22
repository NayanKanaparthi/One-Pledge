"""The neutral cleaning case table, run against the bank agent's cleaning and the test kit's oracle.

`contracts/examples/cleaning_cases.json` was written by hand from SPEC 10.4 and
SPEC_DECISIONS 13 and 45 by none of the implementations it checks (SPEC_DECISIONS 50).
Every case is asserted on both. The bank agent must give exactly the problems (as a
multiset), the action and the canonical value. The oracle decides only whether a value
is kept and what it is, so it is held to the action (kept or refused) and the canonical
value.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from typing import Any

import pytest

from bank_testkit.data import CONTRACTS
from bank_testkit.independent import cell_value, row_values
from onepledge_bank_agent.domain.cleaning.amounts import balance_rules, clean_amount
from onepledge_bank_agent.domain.cleaning.dates import clean_origination_date
from onepledge_bank_agent.domain.cleaning.header import (
    BORROWER_REF,
    CURRENT_BALANCE,
    LOAN_NUMBER,
    ORIGINAL_AMOUNT,
    ORIGINATION_DATE,
    VIN,
    HeaderMap,
)
from onepledge_bank_agent.domain.cleaning.model import CleanedRow, FieldResult
from onepledge_bank_agent.domain.cleaning.rows import clean_field, clean_row
from onepledge_bank_agent.domain.cleaning.text_fields import clean_borrower_ref, clean_loan_number
from onepledge_bank_agent.domain.cleaning.vin_field import clean_vin
from onepledge_bank_agent.domain.enums import CleaningAction, TapeKind
from onepledge_bank_agent.domain.values import CleaningIssue, Money

pytestmark = pytest.mark.unit

TABLE = json.loads((CONTRACTS / "examples" / "cleaning_cases.json").read_text(encoding="utf-8"))
CASES: list[dict[str, Any]] = TABLE["cases"]
type Outcome = tuple[str, Counter[tuple[str, str]], object]


def test_the_table_is_the_size_decision_50_requires() -> None:
    assert len(CASES) >= 120
    assert len({case["id"] for case in CASES}) == len(CASES)


def _action(issues: tuple[CleaningIssue, ...]) -> str:
    if any(issue.action is CleaningAction.REJECTED for issue in issues):
        return "REJECTED"
    return "FIXED" if issues else "ACCEPT"


def _plain(value: object) -> object:
    """A cleaned value in the table's form: cents for money, ISO text for a date, text otherwise."""
    if isinstance(value, Money):
        return value.cents
    if isinstance(value, date):
        return value.isoformat()
    inner = getattr(value, "value", value)
    return inner if isinstance(inner, str | int) else str(inner)


def _field(case: dict[str, Any]) -> tuple[tuple[CleaningIssue, ...], object]:
    field, text, as_of = case["field"], case["input"], date.fromisoformat(case["as_of_date"])
    result: FieldResult[Any]
    across: tuple[CleaningIssue, ...] = ()
    if field == VIN:
        result = clean_field(VIN, text, clean_vin)
    elif field == LOAN_NUMBER:
        result = clean_field(LOAN_NUMBER, text, clean_loan_number)
    elif field == BORROWER_REF:
        result = clean_field(BORROWER_REF, text, clean_borrower_ref)
    elif field == ORIGINATION_DATE:
        result = clean_field(ORIGINATION_DATE, text, lambda t: clean_origination_date(t, as_of))
    elif field == ORIGINAL_AMOUNT:
        result = clean_field(ORIGINAL_AMOUNT, text, lambda t: clean_amount(ORIGINAL_AMOUNT, t))
        across = balance_rules(result.value, None)
    else:
        result = clean_field(CURRENT_BALANCE, text, lambda t: clean_amount(CURRENT_BALANCE, t))
        across = balance_rules(None, result.value)
    issues = (*result.issues, *across)
    rejected = any(issue.action is CleaningAction.REJECTED for issue in issues)
    return issues, None if rejected or result.value is None else _plain(result.value)


def _row(case: dict[str, Any]) -> tuple[tuple[CleaningIssue, ...], object]:
    kind = TapeKind(case["kind"])
    header_map = HeaderMap.from_header(case["input"]["header"], kind)
    outcome = clean_row(case["input"]["cells"], header_map, kind, date.fromisoformat(case["as_of_date"]))
    if not isinstance(outcome, CleanedRow):
        return outcome.issues, None
    if outcome.loan is None:
        return outcome.issues, {"vin": outcome.vin.value}
    loan = outcome.loan
    return outcome.issues, {
        "lender_loan_number": _plain(loan.lender_loan_number),
        "vin": outcome.vin.value,
        "borrower_ref": _plain(loan.terms.borrower_ref),
        "origination_date": loan.terms.origination_date.isoformat(),
        "original_amount": loan.terms.original_amount.cents,
        "current_balance": loan.current_balance.cents,
    }


def bank_agent(case: dict[str, Any]) -> Outcome:
    issues, canonical = _row(case) if case["field"] == "row" else _field(case)
    problems = Counter((issue.field, issue.problem.value) for issue in issues)
    return _action(issues), problems, canonical


def expected(case: dict[str, Any]) -> Outcome:
    wanted = case["expected"]
    problems = Counter((p["field"], p["problem"]) for p in wanted["problems"])
    return wanted["action"], problems, wanted["canonical"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_the_bank_agent_cleaning_gives_every_case_its_expected_outcome(case: dict[str, Any]) -> None:
    assert bank_agent(case) == expected(case), case["note"]


def oracle(case: dict[str, Any]) -> object:
    as_of = date.fromisoformat(case["as_of_date"])
    if case["field"] == "row":
        cells = case["input"]
        return row_values(cells["header"], cells["cells"], case["kind"] == "RELEASE", as_of)
    return cell_value(case["field"], case["input"], as_of)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_the_independent_oracle_keeps_and_refuses_what_every_case_expects(case: dict[str, Any]) -> None:
    assert oracle(case) == case["expected"]["canonical"], case["note"]
