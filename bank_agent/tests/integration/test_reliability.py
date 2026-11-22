"""Crash, resume and fault tests of the whole pipeline (SPEC 13.2, 13.7, 21.3).

Every crashed run unwinds with an exception the product never catches, then a new
worker instance resumes on the same database. Its final state is compared with an
uninterrupted run of the same tape on a second database and a second registry,
never with literals: the same loan states, the same tokens (both runs share the
key holders' scalars), the same registry locks, the same number of distinct
idempotency keys, and exactly one registry submission per key.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import pytest

from bank_testkit.audit_chain import first_broken_seq
from bank_testkit.clocks import ManualClock
from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase, drop, new_bank_database, pairs, provision_and_migrate
from bank_testkit.doubles import KEY_HOLDER_URLS, REGISTRY_URL, HolderScalars, InProcessDoubles
from bank_testkit.independent import accepted_rows, held_vins
from bank_testkit.outside import InProcessOutsideRecords, OutsideService
from bank_testkit.pipeline import BankAgentHarness, Services
from bank_testkit.world import CrashError, Faults
from onepledge_bank_agent.application.advance_submission import StageOutcome
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.domain.enums import TapeKind, TapeStatus
from onepledge_bank_agent.domain.errors import TapeDuplicateError
from onepledge_bank_agent.domain.tape import TapeSubmission
from onepledge_bank_agent.infrastructure.http.base import RETRY_DELAYS_SECONDS
from onepledge_bank_agent.infrastructure.stop_signal import EventStopSignal

pytestmark = pytest.mark.integration

TAPE = "tapes/HARBOR/S01_HARBOR-SUMMIT_BACK_BOOK.csv"
FACILITY, KIND, AS_OF = "HARBOR-SUMMIT", TapeKind.BACK_BOOK, date(2026, 9, 30)
LENDER = "Summit Auto Finance LLC"
CALL_ATTEMPTS = 1 + len(RETRY_DELAYS_SECONDS)
LATER = 3600
type State = dict[str, list[tuple[object, ...]]]
STATE_QUERIES = {
    "tape": "SELECT status, row_count, coalesce(failure_code, '') FROM tape_submissions",
    "items": "SELECT i.row_number, i.disposition, l.vin FROM submission_items i "
    "LEFT JOIN loans l USING (loan_id) ORDER BY 1",
    "issues": "SELECT r.row_number, c.field, c.problem, c.action FROM cleaning_issues c "
    "JOIN loans_raw r USING (raw_id) ORDER BY 1, 2, 3",
    "loans": "SELECT vin, state, borrower_ref, origination_date, original_amount_cents, "
    "current_balance_cents FROM loans ORDER BY vin",
    "checks": "SELECT l.vin, c.check_type, c.result, c.reason_code, c.evidence::text FROM local_checks c "
    "JOIN loans l USING (loan_id) ORDER BY 1, 2",
    "raw": "SELECT row_number, row_sha256 FROM loans_raw ORDER BY 1",
    "tokens": "SELECT l.vin, t.token_kind, t.key_version, t.token FROM loan_tokens t "
    "JOIN loans l USING (loan_id) ORDER BY 1, 2",
    "requests": "SELECT origin, kind, batch_index, item_count, response_status IS NOT NULL, "
    "certificate_id IS NOT NULL FROM registry_requests ORDER BY 1, 2, 3",
    "keys": "SELECT count(DISTINCT idempotency_key), count(*) FROM registry_requests",
    "results": "SELECT r.batch_index, i.position, l.vin, i.result FROM registry_request_items i "
    "JOIN registry_requests r USING (request_id) JOIN loans l USING (loan_id) ORDER BY 1, 2",
    "certificates": "SELECT verified, count(*) FROM certificates GROUP BY 1",
    "alerts": "SELECT count(*) FROM alerts",
    "audits": "SELECT action, detail->>'to', count(*) FROM audit_log "
    "WHERE action NOT IN ('TAPE_STAGE_ATTEMPT_FAILED', 'REGISTRY_REQUEST_SENT', 'TAPE_RETRY_REQUESTED', "
    "'CHECKPOINT_VERIFIED', 'CHECKPOINT_RECEIVED') GROUP BY 1, 2 ORDER BY 1, 2",
}


def state(database: BankDatabase, doubles: InProcessDoubles) -> State:
    """Everything the pipeline derives, keyed so two banks compare equal when they hold the same facts."""
    with database.connect("admin") as connection:
        found = {name: connection.execute(query).fetchall() for name, query in STATE_QUERIES.items()}
    found["registry_locks"] = [(token,) for token in doubles.active_locks("HARBOR")]
    found["registry_submissions"] = [(doubles.member_stats("HARBOR").get("submissions", 0),)]
    return found


def without(audits: list[tuple[object, ...]], action: str) -> list[tuple[object, ...]]:
    """The audit counts without one action."""
    return [row for row in audits if row[0] != action]


def one_submission_per_key(found: State) -> bool:
    """The registry stored exactly one submission for each distinct idempotency key the bank planned."""
    distinct, _ = found["keys"][0]
    return found["registry_submissions"] == [(distinct,)]


def chain_holds(database: BankDatabase) -> bool:
    """The audit chain, recomputed independently, holds and is not empty."""
    entries = pairs(database, "SELECT 'entries', count(*)::text FROM audit_log")
    return first_broken_seq(database) is None and entries != {"entries": "0"}


def batch_items(data: Path) -> str:
    """A `SUBMIT_BATCH_ITEMS` that splits the tape's eligible loans into exactly three batches."""
    accepted = accepted_rows(data / TAPE, AS_OF)
    eligible = len(accepted) - len(held_vins(accepted, data, LENDER, AS_OF))
    size = math.ceil(eligible / 3)
    assert math.ceil(eligible / size) == 3, eligible
    return str(size)


@dataclass(frozen=True)
class Bank:
    """One bank database, its own registry and key holders, and the outside records it calls."""

    database: BankDatabase
    doubles: InProcessDoubles
    outside: OutsideService


@dataclass
class Lab:
    """Fresh seeded banks, the data set, outside records and the shared holder scalars."""

    new_database: Callable[[], BankDatabase]
    data: Path
    outside: InProcessOutsideRecords
    scalars: HolderScalars
    batch: str

    def bank(self) -> Bank:
        database = self.new_database()
        seed_reference(database, self.data)
        return Bank(database, InProcessDoubles(self.scalars), self.outside)

    def harness(
        self,
        bank: Bank,
        *,
        faults: Faults | None = None,
        clock: ManualClock | None = None,
        environment: dict[str, str] | None = None,
    ) -> BankAgentHarness:
        variables = {"SUBMIT_BATCH_ITEMS": self.batch, **(environment or {})}
        services = Services(bank.outside, bank.doubles)
        return BankAgentHarness(bank.database, services, faults=faults, clock=clock, environment=variables)

    def upload(self, harness: BankAgentHarness, as_of: date = AS_OF) -> TapeSubmission:
        return harness.ingest(self.data / TAPE, FACILITY, KIND, as_of)

    def state(self, bank: Bank) -> State:
        return state(bank.database, bank.doubles)


@pytest.fixture(scope="module")
def scalars() -> HolderScalars:
    """The key holders' scalars shared by the clean run and every crashed run of this module."""
    return HolderScalars.fresh()


@pytest.fixture
def lab(
    new_database: Callable[[], BankDatabase],
    small_data: Path,
    small_outside: InProcessOutsideRecords,
    scalars: HolderScalars,
) -> Iterator[Lab]:
    yield Lab(new_database, small_data, small_outside, scalars, batch_items(small_data))
    small_outside.down = False


@pytest.fixture(scope="module")
def clean_run(
    small_data: Path, small_outside: InProcessOutsideRecords, scalars: HolderScalars
) -> Iterator[State]:
    """S01 processed without interruption on its own database and its own registry."""
    database = new_bank_database()
    provision_and_migrate(database)
    seed_reference(database, small_data)
    doubles = InProcessDoubles(scalars)
    environment = {"SUBMIT_BATCH_ITEMS": batch_items(small_data)}
    try:
        with BankAgentHarness(database, Services(small_outside, doubles), environment=environment) as harness:
            harness.ingest(small_data / TAPE, FACILITY, KIND, AS_OF)
            assert harness.run_worker().runs[-1].status is TapeStatus.COMPLETED
        assert chain_holds(database)
        found = state(database, doubles)
        assert len(found["requests"]) == 3 and one_submission_per_key(found)
        yield found
    finally:
        doubles.close()
        drop(database)


CRASH_POINTS = [
    (FaultPoint.AFTER_INTAKE_COMMIT, 0),
    (FaultPoint.CLEAN_BEFORE_COMMIT, 0),
    (FaultPoint.AFTER_CHECK_CHUNK, 0),
    (FaultPoint.AFTER_CHECK_CHUNK, 1),
    (FaultPoint.AFTER_CHECK_CHUNK, 3),
    (FaultPoint.AFTER_KEY_HOLDER_RESPONSE, 0),
    (FaultPoint.AFTER_KEY_HOLDER_RESPONSE, 1),
    (FaultPoint.AFTER_CODES_STORED, 0),
    (FaultPoint.AFTER_REQUESTS_PLANNED, 0),
    (FaultPoint.AFTER_REGISTRY_RESPONSE, 0),
    (FaultPoint.AFTER_REGISTRY_RESPONSE, 1),
    (FaultPoint.AFTER_RESPONSE_STORED, 0),
    (FaultPoint.AFTER_RESPONSE_STORED, 2),
    (FaultPoint.AFTER_CERTIFICATE_STORED, 0),
    (FaultPoint.AFTER_CERTIFICATE_STORED, 1),
    (FaultPoint.BETWEEN_OUTBOX_COMMIT_AND_ACK, 0),
]


@pytest.mark.parametrize("crash", CRASH_POINTS, ids=[f"{point.value}-{skip}" for point, skip in CRASH_POINTS])
def test_a_crash_at_each_point_resumes_to_the_state_of_a_clean_run(
    crash: tuple[FaultPoint, int], lab: Lab, clean_run: State
) -> None:
    bank = lab.bank()
    faults = Faults()
    faults.arm(*crash)
    with lab.harness(bank, faults=faults) as crashing, pytest.raises(CrashError):
        lab.upload(crashing)
        crashing.run_worker()
    assert faults.armed is None, "the crash happened"
    with lab.harness(bank) as resumed:
        resumed.run_worker()
    final = lab.state(bank)
    assert final == clean_run
    assert one_submission_per_key(final)
    assert chain_holds(bank.database)


class StopAt(Faults):
    """Requests a stop on `signal`, as SIGTERM would, the first time the pipeline passes `point`."""

    def __init__(self, point: FaultPoint) -> None:
        super().__init__()
        self._point = point
        self.signal = EventStopSignal()

    def reached(self, point: FaultPoint) -> None:
        super().reached(point)
        if point is self._point:
            self.signal.request_stop()


def four_check_chunks(data: Path) -> str:
    """A `CHECK_CHUNK_LOANS` that splits the tape's accepted loans into exactly four chunks."""
    accepted = len(accepted_rows(data / TAPE, AS_OF))
    size = math.ceil(accepted / 4)
    assert math.ceil(accepted / size) == 4, accepted
    return str(size)


ONE_UNIT = (
    "SELECT 'units', (SELECT count(DISTINCT loan_id) FROM local_checks)::text || ' ' || "
    "(SELECT count(DISTINCT loan_id) FROM loan_tokens)::text || ' ' || "
    "(SELECT count(*) FROM registry_requests WHERE response_status IS NOT NULL)::text || ' ' || "
    "(SELECT count(*) FROM certificates)::text"
)
STOPS = [
    (FaultPoint.AFTER_CHECK_CHUNK, TapeStatus.CLEANED),
    (FaultPoint.AFTER_CODES_STORED, TapeStatus.CHECKED),
    (FaultPoint.AFTER_RESPONSE_STORED, TapeStatus.TOKENIZED),
    (FaultPoint.AFTER_CERTIFICATE_STORED, TapeStatus.SUBMITTED),
]


@pytest.mark.parametrize(("point", "left_at"), STOPS, ids=[point.value for point, _ in STOPS])
def test_a_stop_ends_the_stage_after_the_current_unit_and_a_second_run_completes_it(
    point: FaultPoint, left_at: TapeStatus, lab: Lab, clean_run: State
) -> None:
    """SPEC_DECISIONS 51: the stop flag is honoured after one unit; nothing further is committed."""
    bank, stop = lab.bank(), StopAt(point)
    chunks = {"CHECK_CHUNK_LOANS": four_check_chunks(lab.data)}
    with lab.harness(bank, faults=stop, environment=chunks) as stopping:
        stop.signal = stopping.stop
        submission = lab.upload(stopping)
        runs = stopping.run_worker().runs
    assert [run.outcome for run in runs][-1] is StageOutcome.STOPPED
    assert runs[-1].status is left_at and runs[-1].started_at_status is left_at
    status = pairs(bank.database, "SELECT 'status', status FROM tape_submissions")
    assert status == {"status": left_at.value}
    checked, tokenised, answered, certificates = pairs(bank.database, ONE_UNIT)["units"].split()
    unit_counts = {
        TapeStatus.CLEANED: (checked == chunks["CHECK_CHUNK_LOANS"], tokenised == "0"),
        TapeStatus.CHECKED: (tokenised == "25", answered == "0"),
        TapeStatus.TOKENIZED: (answered == "1", certificates == "0"),
        TapeStatus.SUBMITTED: (answered == "3", certificates == "1"),
    }
    assert unit_counts[left_at] == (True, True), (checked, tokenised, answered, certificates)
    with lab.harness(bank, environment=chunks) as resumed:
        assert resumed.run_worker().runs[-1].status is TapeStatus.COMPLETED
    assert lab.state(bank) == clean_run
    assert one_submission_per_key(lab.state(bank))
    assert chain_holds(bank.database)
    assert submission.submission_id == runs[-1].submission_id


@pytest.mark.parametrize("failures", [1, CALL_ATTEMPTS], ids=["replayed-in-call", "replayed-on-resume"])
def test_an_answer_lost_after_the_registry_committed_is_replayed_on_resume(
    failures: int, lab: Lab, clean_run: State
) -> None:
    """`AFTER_COMMIT`: the registry stored the first batch but its answer never arrived."""
    bank, clock = lab.bank(), ManualClock()
    with lab.harness(bank, clock=clock) as harness:
        submission = lab.upload(harness)
        harness.run_until(submission, TapeStatus.TOKENIZED)
        bank.doubles.fail_next(
            REGISTRY_URL, count=failures, status=503, when="AFTER_COMMIT", path="/v1/submissions"
        )
        harness.run_worker()
        clock.advance(LATER)
        harness.run_worker()
    sent = [r["body"] for r in bank.doubles.received() if r["path"] == "/v1/submissions"]
    assert len(sent) == 3 + failures and len(set(sent)) == 3, "each retry sent the identical body"
    first = "SELECT attempts::text, response_status::text FROM registry_requests WHERE batch_index = 0"
    assert pairs(bank.database, first) == {str(failures + 1): "200"}, "counted every send; a replay"
    final = lab.state(bank)
    assert final == clean_run
    assert one_submission_per_key(final)


class ArmAfterFirstBatch(Faults):
    """Makes the registry fail every attempt of the next submission once the first batch is stored."""

    def __init__(self, doubles: InProcessDoubles) -> None:
        super().__init__()
        self._doubles = doubles

    def reached(self, point: FaultPoint) -> None:
        if point is FaultPoint.AFTER_RESPONSE_STORED and FaultPoint.AFTER_RESPONSE_STORED not in self.passed:
            self._doubles.fail_next(REGISTRY_URL, count=CALL_ATTEMPTS, status=503, when="BEFORE_PROCESSING")
        super().reached(point)


def test_a_registry_fault_on_the_middle_batch_keeps_the_first_and_resumes_the_rest(
    lab: Lab, clean_run: State
) -> None:
    bank, clock = lab.bank(), ManualClock()
    with lab.harness(bank, faults=ArmAfterFirstBatch(bank.doubles), clock=clock) as harness:
        submission = lab.upload(harness)
        harness.run_until(submission, TapeStatus.TOKENIZED)
        assert [run.outcome.value for run in harness.run_worker().runs] == ["RETRY_SCHEDULED"]
        answered = "SELECT batch_index::text, (response_status IS NOT NULL)::text FROM registry_requests"
        assert pairs(bank.database, answered) == {"0": "true", "1": "false", "2": "false"}
        first = pairs(
            bank.database,
            "SELECT 'locked', count(*)::text FROM registry_request_items i JOIN registry_requests r "
            "USING (request_id) JOIN loans l USING (loan_id) WHERE r.batch_index = 0 AND l.state = 'LOCKED'",
        )
        assert int(first["locked"]) == len(bank.doubles.active_locks("HARBOR")) > 0
        clock.advance(LATER)
        harness.run_worker()
    final = lab.state(bank)
    assert final == clean_run, "no duplicate lock and no lost batch"
    assert one_submission_per_key(final)


def test_a_key_holder_2_fault_after_key_holder_1_answered_stores_no_token_until_resumed(
    lab: Lab, clean_run: State
) -> None:
    bank, clock = lab.bank(), ManualClock()
    with lab.harness(bank, clock=clock) as harness:
        submission = lab.upload(harness)
        harness.run_until(submission, TapeStatus.CHECKED)
        bank.doubles.fail_next(
            KEY_HOLDER_URLS["kh2"], count=CALL_ATTEMPTS, status=503, when="BEFORE_PROCESSING"
        )
        assert [run.outcome.value for run in harness.run_worker().runs] == ["RETRY_SCHEDULED"]
        assert bank.doubles.member_stats("HARBOR", KEY_HOLDER_URLS["kh1"])["requests"] == 1
        assert "HARBOR" not in [
            code for code in bank.doubles.members if bank.doubles.member_stats(code, KEY_HOLDER_URLS["kh2"])
        ]
        assert pairs(bank.database, "SELECT 'tokens', count(*)::text FROM loan_tokens") == {"tokens": "0"}
        failed = (
            "SELECT detail->>'stage', concat_ws(' ', detail->>'failure_code', detail->>'dependency', "
            "detail->>'http_status', detail->>'reason') FROM audit_log "
            "WHERE action = 'TAPE_STAGE_ATTEMPT_FAILED'"
        )
        assert pairs(bank.database, failed) == {
            "TOKENIZED": "KEY_HOLDER_UNAVAILABLE key_holder_2 503 SERVER_ERROR"
        }
        clock.advance(LATER)
        harness.run_worker()
    assert lab.state(bank) == clean_run


def test_an_outside_records_fault_for_a_whole_stage_delays_the_tape_and_corrupts_nothing(
    lab: Lab, clean_run: State
) -> None:
    bank, clock = lab.bank(), ManualClock()
    with lab.harness(bank, clock=clock) as harness:
        lab.upload(harness)
        lab.outside.down = True
        assert [run.outcome.value for run in harness.run_worker().runs] == ["ADVANCED", "RETRY_SCHEDULED"]
        assert pairs(bank.database, "SELECT status, attempts::text FROM tape_submissions") == {"CLEANED": "1"}
        assert pairs(bank.database, "SELECT 'checks', count(*)::text FROM local_checks") == {"checks": "0"}
        lab.outside.down = False
        assert harness.run_worker().runs == [], "the backoff is respected"
        clock.advance(2)
        harness.run_worker()
    assert lab.state(bank) == clean_run


OUTAGES = [
    ("outside", "OUTSIDE_RECORDS_UNAVAILABLE", "CLEANED", "CHECKED"),
    ("registry", "REGISTRY_UNAVAILABLE", "TOKENIZED", "SUBMITTED"),
    ("kh1", "KEY_HOLDER_UNAVAILABLE", "CHECKED", "TOKENIZED"),
]


@pytest.mark.parametrize("outage", OUTAGES, ids=[outage[0] for outage in OUTAGES])
def test_the_maximum_number_of_attempts_fails_the_tape_and_a_retry_resumes_it_to_a_clean_run(
    outage: tuple[str, str, str, str], lab: Lab, clean_run: State
) -> None:
    service, code, resume, stage = outage
    bank, clock = lab.bank(), ManualClock()
    with lab.harness(bank, clock=clock, environment={"PIPELINE_MAX_ATTEMPTS": "3"}) as harness:
        submission = lab.upload(harness)
        if service == "outside":
            lab.outside.down = True
        else:
            bank.doubles.down.add(REGISTRY_URL if service == "registry" else KEY_HOLDER_URLS[service])
        for _ in range(4):
            harness.run_worker()
            clock.advance(LATER)
        failed = (
            "SELECT status, failure_code || ' ' || resume_status || ' ' || failed_stage FROM tape_submissions"
        )
        assert pairs(bank.database, failed) == {"FAILED": f"{code} {resume} {stage}"}
        actions = (
            "SELECT action, count(*)::text FROM audit_log "
            "WHERE action IN ('TAPE_STAGE_ATTEMPT_FAILED', 'TAPE_FAILED') GROUP BY 1"
        )
        assert pairs(bank.database, actions) == {"TAPE_STAGE_ATTEMPT_FAILED": "3", "TAPE_FAILED": "1"}
        lab.outside.down = False
        bank.doubles.down.clear()
        assert harness.retry(submission).status.value == resume
        harness.run_worker()
    final = lab.state(bank)
    assert {**final, "audits": without(final["audits"], "TAPE_FAILED")} == clean_run
    retried = "SELECT action, count(*)::text FROM audit_log WHERE action = 'TAPE_RETRY_REQUESTED' GROUP BY 1"
    assert pairs(bank.database, retried) == {"TAPE_RETRY_REQUESTED": "1"}


def test_a_duplicate_upload_fault_is_refused_and_changes_nothing(lab: Lab) -> None:
    bank = lab.bank()
    with lab.harness(bank) as harness:
        lab.upload(harness)
        harness.run_worker()
        before = lab.state(bank)
        for as_of in (AS_OF, date(2026, 9, 1)):
            with pytest.raises(TapeDuplicateError):
                lab.upload(harness, as_of)
    after = lab.state(bank)
    assert [row for row in after["audits"] if row[0] == "TAPE_UPLOAD_REJECTED"] == [
        ("TAPE_UPLOAD_REJECTED", None, 2)
    ]
    assert {**after, "audits": []} == {**before, "audits": []}
    assert one_submission_per_key(after)


def test_a_second_worker_fault_on_one_database_never_runs_a_stage_twice(lab: Lab, clean_run: State) -> None:
    bank = lab.bank()
    with lab.harness(bank) as uploader:
        lab.upload(uploader)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def work() -> None:
        try:
            with lab.harness(bank) as harness:
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
    final = lab.state(bank)
    assert final == clean_run
    advanced = [row for row in final["audits"] if row[0] == "TAPE_STAGE_ADVANCED"]
    assert advanced == [
        ("TAPE_STAGE_ADVANCED", status, 1)
        for status in ("CHECKED", "CLEANED", "COMPLETED", "SUBMITTED", "TOKENIZED")
    ]
    assert one_submission_per_key(final)
    assert chain_holds(bank.database)


class WrongKey:
    """The real outside records service, called with a key it does not know (a configuration fault)."""

    api_key = "opk_" + "w" * 43

    def __init__(self, outside: InProcessOutsideRecords) -> None:
        self.client_factory = outside.client_factory


def test_a_refused_key_fault_fails_the_tape_at_once_and_the_audit_names_the_reason(lab: Lab) -> None:
    bank = lab.bank()
    with lab.harness(replace(bank, outside=WrongKey(lab.outside))) as harness:
        lab.upload(harness)
        outcomes = [run.outcome.value for run in harness.run_worker().runs]
    assert outcomes == ["ADVANCED", "FAILED"]
    failed = "SELECT status, failure_code || ' ' || attempts FROM tape_submissions"
    assert pairs(bank.database, failed) == {"FAILED": "OUTSIDE_RECORDS_REJECTED 0"}
    detail = (
        "SELECT detail->>'failure_code', concat_ws(' ', detail->>'dependency', detail->>'http_status', "
        "detail->>'reason') FROM audit_log WHERE action = 'TAPE_FAILED'"
    )
    assert pairs(bank.database, detail) == {"OUTSIDE_RECORDS_REJECTED": "outside_records 401 UNAUTHENTICATED"}
    assert pairs(bank.database, "SELECT 'checks', count(*)::text FROM local_checks") == {"checks": "0"}
    assert chain_holds(bank.database)


def test_a_refused_key_holder_key_fault_fails_the_tape_at_once_and_sends_nothing(lab: Lab) -> None:
    bank = lab.bank()
    environment = {"KEY_HOLDER_2_API_KEY": "khk_" + "w" * 43}
    with lab.harness(bank, environment=environment) as harness:
        submission = lab.upload(harness)
        harness.run_until(submission, TapeStatus.CHECKED)
        outcomes = [run.outcome.value for run in harness.run_worker().runs]
    assert outcomes == ["FAILED"]
    detail = (
        "SELECT detail->>'failure_code', concat_ws(' ', detail->>'dependency', detail->>'http_status', "
        "detail->>'reason') FROM audit_log WHERE action = 'TAPE_FAILED'"
    )
    assert pairs(bank.database, detail) == {"KEY_HOLDER_REJECTED": "key_holder_2 401 UNAUTHENTICATED"}
    assert pairs(bank.database, "SELECT 'tokens', count(*)::text FROM loan_tokens") == {"tokens": "0"}
    assert bank.doubles.member_stats("HARBOR").get("submissions", 0) == 0


def test_a_lost_answer_on_a_one_batch_tape_is_replayed_on_resume_and_locks_once(lab: Lab) -> None:
    """REL-04: the registry commits the only batch and its answer is lost; the retry replays it."""
    bank = lab.bank()
    bank.doubles.fail_next(REGISTRY_URL, count=1, status=503, when="AFTER_COMMIT")
    with BankAgentHarness(bank.database, Services(bank.outside, bank.doubles)) as harness:
        lab.upload(harness)
        assert harness.run_worker().runs[-1].status is TapeStatus.COMPLETED
    assert pairs(bank.database, "SELECT attempts::text, response_status::text FROM registry_requests") == {
        "2": "200"
    }
    assert bank.doubles.member_stats("HARBOR")["submissions"] == 1
    locked = pairs(bank.database, "SELECT 'locked', count(*)::text FROM loans WHERE state = 'LOCKED'")
    assert int(locked["locked"]) == len(bank.doubles.active_locks("HARBOR"))
    bodies = [r["body"] for r in bank.doubles.received() if r["path"] == "/v1/submissions"]
    assert (len(bodies), len(set(bodies))) == (2, 1)
