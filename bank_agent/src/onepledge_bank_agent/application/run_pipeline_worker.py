"""`RunPipelineWorker`: the loop that moves submissions through the pipeline (SPEC 13.1).

Each step: run the periodic tasks that are due (a task that fails is logged and reported,
and never stops the step), pick the next runnable submission
(upload order, one facility's tapes strictly in order, backoff respected), take its
advisory lock, and run exactly one stage. A submission whose lock another worker holds
is skipped. With nothing to do the worker waits `PIPELINE_POLL_SECONDS`, waking early
on a stop request. A stop is honoured after the unit in progress (SPEC_DECISIONS 51):
the stage commits nothing further and resumes on the next start; no new stage starts.

`worker --once` (SPEC_DECISIONS 101) does all the work it can and then reports whether the
run was clean: its last outbox sync was complete, the bank is not halted, and no tape
ended FAILED during the run. Its periodic tasks wait for a sync running elsewhere.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from onepledge_bank_agent.application.advance_submission import AdvanceSubmission, StageRun
from onepledge_bank_agent.application.error_records import error_code_of, http_facts
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import SubmissionLocks, UnitOfWorkFactory
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.enums import TapeStatus

CANDIDATES_PER_PICK = 20


class PeriodicResult(Protocol):
    """Whether a periodic run took in everything it should have, and why not."""

    @property
    def complete(self) -> bool:
        """True when nothing was left behind."""
        ...

    @property
    def reason(self) -> str | None:
        """The first reason it was incomplete."""
        ...


class PeriodicTask(Protocol):
    """Work the worker runs on a fixed interval between stages (for example a registry sync)."""

    @property
    def interval_seconds(self) -> float:
        """Seconds between two runs."""
        ...

    def run(self, wait: bool = False) -> PeriodicResult:
        """Run once; must not raise for an expected dependency outage. With `wait` a run going on
        elsewhere is waited for instead of skipped."""
        ...


class HaltState(Protocol):
    """The halt rule as the worker reports it."""

    def active(self) -> bool:
        """True when nothing state changing may be sent to the registry."""
        ...


@dataclass
class WorkerReport:
    """What a worker run did, stage by stage; the error code of each periodic task that failed; the
    reason the last run of a periodic task was incomplete; whether the bank ended halted."""

    runs: list[StageRun] = field(default_factory=list)
    task_failures: list[str] = field(default_factory=list)
    incomplete: str | None = None
    halted: bool = False

    @property
    def failed_tapes(self) -> list[uuid.UUID]:
        """The submissions that ended FAILED during the run, once each, in order."""
        return list(dict.fromkeys(r.submission_id for r in self.runs if r.status is TapeStatus.FAILED))

    @property
    def clean(self) -> bool:
        """`worker --once` exits 0 only when this holds (SPEC_DECISIONS 101)."""
        return not (self.task_failures or self.incomplete or self.halted or self.failed_tapes)


@dataclass(frozen=True)
class WorkerSettings:
    """The idle poll interval of SPEC 3.3, the periodic tasks registered with the worker, and the halt
    rule it reports at the end of `--once`."""

    poll_seconds: float
    periodic: Sequence[PeriodicTask] = ()
    halt: HaltState = field(kw_only=True)


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
        while not self._stop.is_set() and (run := self._step(report, wait=True)) is not None:
            report.runs.append(run)
        report.halted = self._settings.halt.active()
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

    def _step(self, report: WorkerReport, wait: bool = False) -> StageRun | None:
        self._run_due_tasks(report, wait)
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

    def _run_due_tasks(self, report: WorkerReport, wait: bool) -> None:
        """Run the due tasks; whatever one does, including raising, the worker goes on to the stages."""
        now = self._clock.now()
        for index, task in enumerate(self._periodic):
            last = self._last_run.get(index)
            if last is None or (now - last).total_seconds() >= task.interval_seconds:
                self._last_run[index] = now
                try:
                    result = task.run(wait)
                    report.incomplete = None if result.complete else (result.reason or "INCOMPLETE")
                except Exception as error:  # SPEC_DECISIONS 85 rule 5: a sync can never starve the stages
                    code = error_code_of(error)
                    report.task_failures.append(code)
                    self._log.failure(
                        LogEvent.PERIODIC_TASK_FAILED, error, error_code=code, **http_facts(error)
                    )
