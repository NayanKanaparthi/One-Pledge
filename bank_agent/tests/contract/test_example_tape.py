"""The committed 20 loan example (SPEC 5.9): intake, cleaning and local checks reproduce its expected file."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from bank_testkit.data import EXAMPLE_TAPES, seed_reference
from bank_testkit.database import BankDatabase
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from onepledge_bank_agent.domain.enums import TapeKind, TapeStatus

pytestmark = [pytest.mark.contract, pytest.mark.integration]

TAPE = EXAMPLE_TAPES / "example_tape_20.csv"
EXPECTED = EXAMPLE_TAPES / "example_tape_20.expected.json"


def _issue_key(issue: dict[str, str]) -> tuple[str, str]:
    return issue["field"], issue["problem"]


def _observed(database: BankDatabase, submission_id: object) -> dict[int, dict[str, Any]]:
    with database.connect("admin") as connection:
        items = connection.execute(
            "SELECT i.row_number, i.disposition, i.raw_id, l.state, i.loan_id FROM submission_items i "
            "LEFT JOIN loans l USING (loan_id) WHERE i.submission_id = %s",
            (submission_id,),
        ).fetchall()
        rows: dict[int, dict[str, Any]] = {}
        for row_number, disposition, raw_id, state, loan_id in items:
            issues = connection.execute(
                "SELECT field, problem, action FROM cleaning_issues WHERE raw_id = %s "
                "ORDER BY field, problem",
                (raw_id,),
            ).fetchall()
            checks = connection.execute(
                "SELECT check_type, result, reason_code FROM local_checks WHERE loan_id = %s "
                "ORDER BY check_type",
                (loan_id,),
            ).fetchall()
            rows[int(str(row_number))] = {
                "disposition": disposition,
                "cleaning_issues": [{"field": f, "problem": p, "action": a} for f, p, a in issues],
                "checks": [{"check_type": c, "result": r, "reason_code": n} for c, r, n in checks],
                "local_state": state,
            }
    return rows


def test_the_example_tape_matches_its_expected_file(
    bank_db: BankDatabase,
    example_data: Path,
    example_outside: InProcessOutsideRecords,
    doubles: InProcessDoubles,
) -> None:
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    seed_reference(bank_db, example_data, "HARBOR")
    with BankAgentHarness(bank_db, Services(example_outside, doubles)) as bank:
        submission = bank.ingest(
            TAPE,
            expected["facility_code"],
            TapeKind(expected["kind"]),
            date.fromisoformat(expected["as_of_date"]),
        )
        reached = bank.run_until(submission, TapeStatus.CHECKED)
    assert reached is TapeStatus.CHECKED

    observed = _observed(bank_db, submission.submission_id)
    assert sorted(observed) == list(range(1, 21))
    for row in expected["rows"]:
        seen = observed[row["row_number"]]
        assert seen["disposition"] == row["disposition"], row["row_number"]
        issues = sorted(seen["cleaning_issues"], key=_issue_key)
        assert issues == sorted(row["cleaning_issues"], key=_issue_key), row["row_number"]
        assert seen["checks"] == sorted(row["checks"], key=lambda check: check["check_type"]), row[
            "row_number"
        ]
        assert seen["local_state"] == row["local_state"], row["row_number"]
