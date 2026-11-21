"""`RunPipelineWorker`: the loop that moves submissions through the pipeline (SPEC 13.1).

Each step: run the periodic tasks that are due, pick the next runnable submission
(upload order, one facility's tapes strictly in order, backoff respected), take its
advisory lock, and run exactly one stage. A submission whose lock another worker holds
is skipped. With nothing to do the worker waits `PIPELINE_POLL_SECONDS`, waking early
on a stop request; it stops between stages, so no stage is left half committed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from onepledge_bank_agent.application.advance_submission import AdvanceSubmission, StageRun
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import StopSignal, SubmissionLocks, UnitOfWorkFactory
from onepledge_bank_agent.application.runtime import Runtime

CANDIDATES_PER_PICK = 20


class PeriodicTask(Protocol):
    """Work the worker runs on a fixed interval between stages (for example a registry sync)."""

    @property
    def interval_seconds(self) -> float:
        """Seconds between two runs."""
        ...

    def run(self) -> None:
        """Run once; must not raise for an expected dependency outage."""
        ...


@dataclass
class WorkerReport:
    """What a worker run did, stage by stage."""

    runs: list[StageRun] = field(default_factory=list)


@dataclass(frozen=True)
class WorkerSettings:
    """The idle poll interval of SPEC 3.3 and the periodic tasks registered with the worker."""

    poll_seconds: float
    periodic: Sequence[PeriodicTask] = ()


class RunPipelineWorker:
    """Advances submissions one stage at a time until there is nothing runnable or a stop is requested."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        advance: AdvanceSubmission,
        locks: SubmissionLocks,
        runtime: Runtime,
        settings: WorkerSettings,
    ) -> None:
        self._units = units
        self._advance = advance
        self._locks = locks
        self._clock = runtime.clock
        self._log = runtime.log
        self._settings = settings
        self._periodic = tuple(settings.periodic)
        self._last_run: dict[int, datetime] = {}

    def run_once(self) -> WorkerReport:
        """Process until no submission is runnable (`worker --once`)."""
        report = WorkerReport()
        while (run := self._step()) is not None:
            report.runs.append(run)
        return report

    def run_until_stopped(self, stop: StopSignal) -> WorkerReport:
        """Process until a stop is requested, waiting the poll interval whenever idle."""
        report = WorkerReport()
        self._log.info(LogEvent.WORKER_STARTED)
        while not stop.is_set():
            try:
                run = self._step()
            except Exception as error:  # a failed step (database down) must not end the worker
                self._log.failure(LogEvent.WORKER_STEP_FAILED, error)
                run = None
            if run is not None:
                report.runs.append(run)
            elif stop.wait(self._settings.poll_seconds):
                break
        self._log.info(LogEvent.WORKER_STOPPED, count=len(report.runs))
        return report

    def _step(self) -> StageRun | None:
        self._run_due_tasks()
        now = self._clock.now()
        with self._units() as unit:
            candidates = unit.tapes.runnable(self._advance.runnable_statuses, now, CANDIDATES_PER_PICK)
        for submission_id in candidates:
            with self._locks.hold(submission_id) as granted:
                if granted:
                    return self._advance.advance(submission_id)
        return None

    def _run_due_tasks(self) -> None:
        now = self._clock.now()
        for index, task in enumerate(self._periodic):
            last = self._last_run.get(index)
            if last is None or (now - last).total_seconds() >= task.interval_seconds:
                task.run()
                self._last_run[index] = now
