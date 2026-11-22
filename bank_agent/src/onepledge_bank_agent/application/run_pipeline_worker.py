"""`RunPipelineWorker`: the loop that moves submissions through the pipeline (SPEC 13.1).

Each step: run the periodic tasks that are due (a task that fails is logged and reported,
and never stops the step), pick the next runnable submission
(upload order, one facility's tapes strictly in order, backoff respected), take its
advisory lock, and run exactly one stage. A submission whose lock another worker holds
is skipped. With nothing to do the worker waits `PIPELINE_POLL_SECONDS`, waking early
on a stop request. A stop is honoured after the unit in progress (SPEC_DECISIONS 51):
the stage commits nothing further and resumes on the next start; no new stage starts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from onepledge_bank_agent.application.advance_submission import AdvanceSubmission, StageRun
from onepledge_bank_agent.application.error_records import error_code_of, http_facts
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import SubmissionLocks, UnitOfWorkFactory
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
    """What a worker run did, stage by stage, and the error code of each periodic task that failed."""

    runs: list[StageRun] = field(default_factory=list)
    task_failures: list[str] = field(default_factory=list)


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
        self._stop = runtime.stop
        self._settings = settings
        self._periodic = tuple(settings.periodic)
        self._last_run: dict[int, datetime] = {}

    def run_once(self) -> WorkerReport:
        """Process until no submission is runnable or a stop is requested (`worker --once`). A periodic
        task that fails is reported in the result; the stages run all the same (SPEC_DECISIONS 85)."""
        report = WorkerReport()
        while not self._stop.is_set() and (run := self._step(report)) is not None:
            report.runs.append(run)
        return report

    def run_until_stopped(self) -> WorkerReport:
        """Process until a stop is requested, waiting the poll interval whenever idle."""
        report = WorkerReport()
        self._log.info(LogEvent.WORKER_STARTED)
        while not self._stop.is_set():
            try:
                run = self._step(report)
            except Exception as error:  # a failed step (database down) must not end the worker
                self._log.failure(
                    LogEvent.WORKER_STEP_FAILED, error, error_code=error_code_of(error), **http_facts(error)
                )
                run = None
            if run is not None:
                report.runs.append(run)
            elif self._stop.wait(self._settings.poll_seconds):
                break
        self._log.info(LogEvent.WORKER_STOPPED, count=len(report.runs))
        return report

    def _step(self, report: WorkerReport) -> StageRun | None:
        self._run_due_tasks(report)
        if self._stop.is_set():
            return None
        now = self._clock.now()
        with self._units() as unit:
            candidates = unit.tapes.runnable(self._advance.runnable_statuses, now, CANDIDATES_PER_PICK)
        for submission_id in candidates:
            with self._locks.hold(submission_id) as granted:
                if granted:
                    return self._advance.advance(submission_id)
        return None

    def _run_due_tasks(self, report: WorkerReport) -> None:
        """Run the due tasks; whatever one does, including raising, the worker goes on to the stages."""
        now = self._clock.now()
        for index, task in enumerate(self._periodic):
            last = self._last_run.get(index)
            if last is None or (now - last).total_seconds() >= task.interval_seconds:
                self._last_run[index] = now
                try:
                    task.run()
                except Exception as error:  # SPEC_DECISIONS 85 rule 5: a sync can never starve the stages
                    code = error_code_of(error)
                    report.task_failures.append(code)
                    self._log.failure(
                        LogEvent.PERIODIC_TASK_FAILED, error, error_code=code, **http_facts(error)
                    )
