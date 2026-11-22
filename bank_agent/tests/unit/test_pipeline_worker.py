"""`RunPipelineWorker` (SPEC 13.1): order, one facility at a time, backoff, locks, periodic tasks, stop."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import date, timedelta

import pytest

from bank_testkit.stages import FakeStage, raising
from bank_testkit.tapes import SUMMIT_LAYOUT
from bank_testkit.world import World
from onepledge_bank_agent.application.advance_submission import AdvanceSubmission
from onepledge_bank_agent.application.run_pipeline_worker import RunPipelineWorker, WorkerSettings
from onepledge_bank_agent.domain.enums import TapeKind, TapeSource, TapeStatus
from onepledge_bank_agent.domain.errors import OutsideRecordsUnavailableError
from onepledge_bank_agent.domain.tape import TapeFacts, TapeSubmission

pytestmark = pytest.mark.unit


class Locks:
    """Advisory locks in memory; `held` are taken by another worker."""

    def __init__(self) -> None:
        self.held: set[uuid.UUID] = set()
        self.taken: list[uuid.UUID] = []

    @contextlib.contextmanager
    def hold(self, submission_id: uuid.UUID) -> Iterator[bool]:
        granted = submission_id not in self.held
        if granted:
            self.taken.append(submission_id)
        yield granted


class Stop:
    """Stops after `steps` idle waits."""

    def __init__(self, idle_waits: int) -> None:
        self.waits = 0
        self._limit = idle_waits

    def is_set(self) -> bool:
        return self.waits >= self._limit

    def wait(self, seconds: float) -> bool:
        self.waits += 1
        return self.is_set()


class Tick:
    """A periodic task counting its runs."""

    def __init__(self, interval: float) -> None:
        self.interval_seconds = interval
        self.runs = 0

    def run(self) -> None:
        self.runs += 1


def upload(world: World, facility: uuid.UUID, minutes: int) -> uuid.UUID:
    submission = uuid.uuid4()
    facts = TapeFacts(
        submission, facility, TapeKind.PLEDGE, TapeSource.FILE_UPLOAD, "t.csv", uuid.uuid4().hex * 2,
        "b" * 64, date(2026, 10, 15), 1, uuid.uuid4(), world.clock.now() + timedelta(minutes=minutes),
    )  # fmt: skip
    with world.units() as unit:
        unit.tapes.add(TapeSubmission.receive(facts), SUMMIT_LAYOUT)
        unit.commit()
    return submission


def worker(
    world: World, locks: Locks, *stages: FakeStage, periodic: tuple[Tick, ...] = ()
) -> RunPipelineWorker:
    advance = AdvanceSubmission(world.units, stages, world.runtime, 3)
    return RunPipelineWorker(world.units, advance, locks, world.runtime, WorkerSettings(0.01, periodic))


def test_once_processes_until_nothing_is_runnable_keeping_each_facility_in_upload_order() -> None:
    world = World()
    a1 = upload(world, world.facility.facility_id, 0)
    b1 = upload(world, world.other_facility.facility_id, 1)
    a2 = upload(world, world.facility.facility_id, 2)
    report = worker(
        world, Locks(), FakeStage(world, TapeStatus.CLEANED), FakeStage(world, TapeStatus.CHECKED)
    ).run_once()
    order = [(run.submission_id, run.status) for run in report.runs]
    assert order[:3] == [(a1, TapeStatus.CLEANED), (a1, TapeStatus.CHECKED), (b1, TapeStatus.CLEANED)]
    assert (a2, TapeStatus.CLEANED) not in order, "a2 waits while a1 of the same facility is unfinished"
    assert {world.store.state.tapes[s].status for s in (a1, b1)} == {TapeStatus.CHECKED}
    assert world.store.state.tapes[a2].status is TapeStatus.RECEIVED


def test_a_submission_locked_by_another_worker_is_skipped() -> None:
    world = World()
    a = upload(world, world.facility.facility_id, 0)
    b = upload(world, world.other_facility.facility_id, 1)
    locks = Locks()
    locks.held.add(a)
    report = worker(world, locks, FakeStage(world, TapeStatus.CLEANED)).run_once()
    assert [run.submission_id for run in report.runs] == [b] and locks.taken == [b]


def test_a_backed_off_submission_waits_until_its_time() -> None:
    world = World()
    submission = upload(world, world.facility.facility_id, 0)
    stage = FakeStage(world, TapeStatus.CLEANED, raising(OutsideRecordsUnavailableError()))
    runner = worker(world, Locks(), stage)
    assert len(runner.run_once().runs) == 1
    assert runner.run_once().runs == []
    world.clock.advance(2)
    stage.behaviour = None
    assert [run.status for run in runner.run_once().runs] == [TapeStatus.CLEANED]
    assert world.store.state.tapes[submission].progress.attempts == 0


def test_periodic_tasks_run_on_their_interval_and_the_worker_stops_on_request() -> None:
    world = World()
    upload(world, world.facility.facility_id, 0)
    tick = Tick(interval=10)
    runner = worker(world, Locks(), FakeStage(world, TapeStatus.CLEANED), periodic=(tick,))
    stop = Stop(idle_waits=2)
    report = runner.run_until_stopped(stop)
    assert [run.status for run in report.runs] == [TapeStatus.CLEANED] and tick.runs == 1 and stop.waits == 2
    world.clock.advance(10)
    runner.run_once()
    assert tick.runs == 2
    events = [event for _, event, _ in world.log.events]
    assert events[0] == "worker_started" and events[-1] == "worker_stopped"


def test_a_failing_step_is_logged_and_the_worker_keeps_going() -> None:
    world = World()
    upload(world, world.facility.facility_id, 0)

    class Broken(Tick):
        def run(self) -> None:
            raise ConnectionError("database down")

    runner = worker(world, Locks(), FakeStage(world, TapeStatus.CLEANED), periodic=(Broken(0),))
    runner.run_until_stopped(Stop(idle_waits=2))
    failures = [fields for level, event, fields in world.log.events if event == "worker_step_failed"]
    assert (
        failures
        and failures[0]["exception_type"] == "ConnectionError"
        and "database down" not in repr(failures)
    )
