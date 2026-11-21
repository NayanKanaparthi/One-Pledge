"""The scripted run of WORK_PACKAGES WP2: S01 of the small profile through intake, clean and check.

Ingested through `IngestTape`, advanced by the worker until CHECKED against in-process
outside records, and compared, row by row, with an independent computation written in
the test kit (never with the answer key).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase
from bank_testkit.independent import accepted_rows, held_vins
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness
from onepledge_bank_agent.domain.enums import TapeKind, TapeStatus

pytestmark = pytest.mark.integration


def _step(data_dir: Path, step_id: str) -> dict[str, str]:
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    step: dict[str, str] = next(s for s in manifest["steps"] if s["step_id"] == step_id)
    return step


def _lender_name(data_dir: Path, facility_code: str) -> str:
    reference = json.loads((data_dir / "reference.json").read_text(encoding="utf-8"))
    facility = next(f for f in reference["facilities"] if f["facility_code"] == facility_code)
    lender = next(entry for entry in reference["lenders"] if entry["lender_code"] == facility["lender_code"])
    name: str = lender["legal_name"]
    return name


@pytest.mark.parametrize("step_id", ["S01", "S04", "S09", "S10"])
def test_a_small_profile_tape_reaches_checked_with_independently_computed_outcomes(
    step_id: str, bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords
) -> None:
    """S01 is the required run; S04 (Lakeside), S09 and S10 (planted holds) widen it, each on a fresh bank."""
    step = _step(small_data, step_id)
    tape, as_of = small_data / step["file"], date.fromisoformat(step["as_of_date"])
    seed_reference(bank_db, small_data, step["bank_code"])
    with BankAgentHarness(bank_db, small_outside) as bank:
        submission = bank.ingest(tape, step["facility_code"], TapeKind(step["kind"]), as_of)
        report = bank.run_worker()

    assert [run.status for run in report.runs] == [TapeStatus.CLEANED, TapeStatus.CHECKED]
    expected_accepted = accepted_rows(tape, as_of)
    expected_held = held_vins(
        expected_accepted, small_data, _lender_name(small_data, step["facility_code"]), as_of
    )
    with bank_db.connect("admin") as connection:
        status = connection.execute(
            "SELECT status, row_count FROM tape_submissions WHERE submission_id = %s",
            (submission.submission_id,),
        ).fetchone()
        dispositions = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT disposition, count(*) FROM submission_items WHERE submission_id = %s GROUP BY 1",
                (submission.submission_id,),
            )
        }
        accepted = {
            row[0]
            for row in connection.execute(
                "SELECT row_number FROM submission_items "
                "WHERE submission_id = %s AND disposition = 'ACCEPTED_NEW'",
                (submission.submission_id,),
            )
        }
        held = {row[0] for row in connection.execute("SELECT vin FROM loans WHERE state = 'HELD_LOCAL'")}
        pending = connection.execute("SELECT count(*) FROM loans WHERE state = 'PENDING'").fetchone()
        checks = connection.execute("SELECT count(*) FROM local_checks").fetchone()

    assert status == ("CHECKED", int(step["data_rows"]))
    assert accepted == {row.row_number for row in expected_accepted}
    assert dispositions == {
        "ACCEPTED_NEW": len(expected_accepted),
        "REJECTED": int(step["data_rows"]) - len(expected_accepted),
    }
    assert held == expected_held
    assert pending == (len(expected_accepted) - len(expected_held),)
    assert checks == (4 * len(expected_accepted),)
    if step_id in ("S09", "S10"):
        assert expected_held, "these steps carry loans the checks must hold"
