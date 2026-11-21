"""Crash, resume and fault tests for intake, clean and check (SPEC 13.2, 13.7, 21.3).

Every crashed run unwinds with an exception the product never catches, then a new
worker instance resumes on the same database. Its final state is compared with an
uninterrupted run of the same tape on a second database, never with literals.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from bank_testkit.audit_chain import first_broken_seq
from bank_testkit.clocks import ManualClock
from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase, drop, new_bank_database, pairs, provision_and_migrate
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness
from bank_testkit.world import CrashError, Faults
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.domain.enums import TapeKind
from onepledge_bank_agent.domain.errors import TapeDuplicateError

pytestmark = pytest.mark.integration

TAPE = "tapes/HARBOR/S01_HARBOR-SUMMIT_BACK_BOOK.csv"
FACILITY, KIND, AS_OF = "HARBOR-SUMMIT", TapeKind.BACK_BOOK, date(2026, 9, 30)
type State = dict[str, list[tuple[object, ...]]]
STATE_QUERIES = {
    "tape": "SELECT status, row_count, attempts, coalesce(failure_code, '') FROM tape_submissions",
    "items": "SELECT i.row_number, i.disposition, l.vin FROM submission_items i "
    "LEFT JOIN loans l USING (loan_id) ORDER BY 1",
    "issues": "SELECT r.row_number, c.field, c.problem, c.action FROM cleaning_issues c "
    "JOIN loans_raw r USING (raw_id) ORDER BY 1, 2, 3",
    "loans": "SELECT vin, state, borrower_ref, origination_date, original_amount_cents, "
    "current_balance_cents FROM loans ORDER BY vin",
    "checks": "SELECT l.vin, c.check_type, c.result, c.reason_code, c.evidence::text FROM local_checks c "
    "JOIN loans l USING (loan_id) ORDER BY 1, 2",
    "raw": "SELECT row_number, row_sha256 FROM loans_raw ORDER BY 1",
    "audits": "SELECT action, detail->>'to', count(*) FROM audit_log "
    "WHERE action <> 'TAPE_STAGE_ATTEMPT_FAILED' GROUP BY 1, 2 ORDER BY 1, 2",
}


def state(database: BankDatabase) -> State:
    """Everything the stages derive, keyed so two databases compare equal when they hold the same facts."""
    with database.connect("admin") as connection:
        return {name: connection.execute(query).fetchall() for name, query in STATE_QUERIES.items()}


def chain_holds(database: BankDatabase) -> bool:
    """The audit chain, recomputed independently, holds and is not empty."""
    entries = pairs(database, "SELECT 'entries', count(*)::text FROM audit_log")
    return first_broken_seq(database) is None and entries != {"entries": "0"}


@dataclass
class Lab:
    """Fresh seeded bank databases, the data set and the shared in-process outside records."""

    new_database: Callable[[], BankDatabase]
    data: Path
    outside: InProcessOutsideRecords

    def bank(self) -> BankDatabase:
        database = self.new_database()
        seed_reference(database, self.data)
        return database

    def upload(self, harness: BankAgentHarness, as_of: date = AS_OF) -> None:
        harness.ingest(self.data / TAPE, FACILITY, KIND, as_of)


@pytest.fixture
def lab(
    new_database: Callable[[], BankDatabase], small_data: Path, small_outside: InProcessOutsideRecords
) -> Iterator[Lab]:
    yield Lab(new_database, small_data, small_outside)
    small_outside.down = False


@pytest.fixture(scope="module")
def clean_run(small_data: Path, small_outside: InProcessOutsideRecords) -> Iterator[State]:
    """S01 processed without interruption on its own database."""
    database = new_bank_database()
    provision_and_migrate(database)
    seed_reference(database, small_data)
    try:
        with BankAgentHarness(database, small_outside) as harness:
            harness.ingest(small_data / TAPE, FACILITY, KIND, AS_OF)
            harness.run_worker()
        assert chain_holds(database)
        yield state(database)
    finally:
        drop(database)


CRASH_POINTS = [
    (FaultPoint.AFTER_INTAKE_COMMIT, 0),
    (FaultPoint.CLEAN_BEFORE_COMMIT, 0),
    (FaultPoint.AFTER_CHECK_CHUNK, 0),
    (FaultPoint.AFTER_CHECK_CHUNK, 1),
    (FaultPoint.AFTER_CHECK_CHUNK, 3),
]


@pytest.mark.parametrize("crash", CRASH_POINTS, ids=[f"{point.value}-{skip}" for point, skip in CRASH_POINTS])
def test_a_crash_at_each_point_resumes_to_the_state_of_a_clean_run(
    crash: tuple[FaultPoint, int], lab: Lab, clean_run: State
) -> None:
    database = lab.bank()
    faults = Faults()
    faults.arm(*crash)
    with BankAgentHarness(database, lab.outside, faults=faults) as crashing, pytest.raises(CrashError):
        lab.upload(crashing)
        crashing.run_worker()
    assert faults.armed is None, "the crash happened"
    with BankAgentHarness(database, lab.outside) as resumed:
        resumed.run_worker()
    assert state(database) == clean_run
    assert chain_holds(database)


def test_an_outage_for_a_whole_stage_delays_the_tape_and_corrupts_nothing(lab: Lab, clean_run: State) -> None:
    database, clock = lab.bank(), ManualClock()
    with BankAgentHarness(database, lab.outside, clock=clock) as harness:
        lab.upload(harness)
        lab.outside.down = True
        assert [run.outcome.value for run in harness.run_worker().runs] == ["ADVANCED", "RETRY_SCHEDULED"]
        assert pairs(database, "SELECT status, attempts::text FROM tape_submissions") == {"CLEANED": "1"}
        assert pairs(database, "SELECT 'held', count(*)::text FROM loans WHERE state = 'HELD_LOCAL'") == {
            "held": "0"
        }
        assert pairs(database, "SELECT 'checks', count(*)::text FROM local_checks") == {"checks": "0"}
        failures = (
            "SELECT action, count(*)::text FROM audit_log "
            "WHERE action = 'TAPE_STAGE_ATTEMPT_FAILED' GROUP BY 1"
        )
        assert pairs(database, failures) == {"TAPE_STAGE_ATTEMPT_FAILED": "1"}
        lab.outside.down = False
        assert harness.run_worker().runs == [], "the backoff is respected"
        clock.advance(2)
        harness.run_worker()
    assert state(database) == clean_run


def test_the_maximum_number_of_attempts_fails_the_tape_with_the_dependency_code(lab: Lab) -> None:
    database, clock = lab.bank(), ManualClock()
    with BankAgentHarness(
        database, lab.outside, clock=clock, environment={"PIPELINE_MAX_ATTEMPTS": "3"}
    ) as harness:
        lab.upload(harness)
        lab.outside.down = True
        for _ in range(3):
            harness.run_worker()
            clock.advance(60)
    failed = (
        "SELECT status, failure_code || ' ' || resume_status || ' ' || failed_stage FROM tape_submissions"
    )
    assert pairs(database, failed) == {"FAILED": "OUTSIDE_RECORDS_UNAVAILABLE CLEANED CHECKED"}
    actions = (
        "SELECT action, count(*)::text FROM audit_log "
        "WHERE action IN ('TAPE_STAGE_ATTEMPT_FAILED', 'TAPE_FAILED') GROUP BY 1"
    )
    assert pairs(database, actions) == {"TAPE_STAGE_ATTEMPT_FAILED": "3", "TAPE_FAILED": "1"}


def test_the_same_tape_uploaded_twice_is_refused_and_changes_nothing(lab: Lab) -> None:
    database = lab.bank()
    with BankAgentHarness(database, lab.outside) as harness:
        lab.upload(harness)
        harness.run_worker()
        before = state(database)
        for as_of in (AS_OF, date(2026, 9, 1)):
            with pytest.raises(TapeDuplicateError):
                lab.upload(harness, as_of)
    after = state(database)
    assert [row for row in after["audits"] if row[0] == "TAPE_UPLOAD_REJECTED"] == [
        ("TAPE_UPLOAD_REJECTED", None, 2)
    ]
    assert {**after, "audits": []} == {**before, "audits": []}


def test_two_workers_on_one_database_never_run_a_stage_twice(lab: Lab, clean_run: State) -> None:
    database = lab.bank()
    with BankAgentHarness(database, lab.outside) as uploader:
        lab.upload(uploader)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def work() -> None:
        try:
            with BankAgentHarness(database, lab.outside) as harness:
                barrier.wait()
                harness.run_worker()
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    final = state(database)
    for name in ("tape", "loans", "checks", "items", "issues", "raw"):
        assert final[name] == clean_run[name], name
    advanced = [row for row in final["audits"] if row[0] == "TAPE_STAGE_ADVANCED"]
    assert advanced == [("TAPE_STAGE_ADVANCED", "CHECKED", 1), ("TAPE_STAGE_ADVANCED", "CLEANED", 1)]
    assert chain_holds(database)
