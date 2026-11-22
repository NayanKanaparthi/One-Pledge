"""`AdvanceSubmission`: run the next stage of one submission and handle its failure (SPEC 13.2, 13.7).

Stages are registered, not chosen by a conditional: each stage names the status it
reaches, and the stage that runs is the one registered for the submission's current
status. Later stages are added by registering them in the composition root.

A stop requested while the stage runs (SPEC_DECISIONS 51) ends it after the unit it was
in: the submission keeps its status and resumes on the next start.

Failure handling: a transient dependency failure counts an attempt and backs off
(`min(2 ** attempts, 60)` seconds), and fails the submission at the configured maximum;
a permanent failure fails it at once with its code; any other error is logged with
its type and frame locations only and fails it with INTERNAL_ERROR.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from onepledge_bank_agent.application.audit_entries import (
    FailureDetail,
    failure_detail,
    stage_attempt_failed,
    tape_failed,
)
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.application.runtime import Runtime, StopRequestedError
from onepledge_bank_agent.domain.enums import TapeStatus
from onepledge_bank_agent.domain.errors import PipelineStageError
from onepledge_bank_agent.domain.tape import NEXT_STAGE

_FROM_STATUS: Mapping[TapeStatus, TapeStatus] = MappingProxyType(
    {reached: before for before, reached in NEXT_STAGE.items()}
)


class Stage(Protocol):
    """One pipeline stage. `run` either advances the submission by one status or raises."""

    @property
    def reached(self) -> TapeStatus:
        """The status the stage reaches."""
        ...

    def run(self, submission_id: uuid.UUID) -> None:
        """Run the stage; does nothing when the submission is no longer at the stage's starting status."""
        ...


class StageOutcome(StrEnum):
    """What one call of `AdvanceSubmission` did."""

    ADVANCED = "ADVANCED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    NOTHING_TO_DO = "NOTHING_TO_DO"


@dataclass(frozen=True)
class StageRun:
    """The submission, the status it started from, the outcome and the status it ended at."""

    submission_id: uuid.UUID
    started_at_status: TapeStatus
    outcome: StageOutcome
    status: TapeStatus
    failure: FailureDetail | None = None


class AdvanceSubmission:
    """Dispatches to the stage registered for a submission's status."""

    def __init__(
        self, units: UnitOfWorkFactory, stages: Iterable[Stage], runtime: Runtime, max_attempts: int
    ) -> None:
        self._units = units
        self._stages: Mapping[TapeStatus, Stage] = MappingProxyType(
            {_FROM_STATUS[stage.reached]: stage for stage in stages}
        )
        self._clock = runtime.clock
        self._log = runtime.log
        self._max_attempts = max_attempts

    @property
    def runnable_statuses(self) -> frozenset[TapeStatus]:
        """The statuses a registered stage starts from."""
        return frozenset(self._stages)

    def advance(self, submission_id: uuid.UUID) -> StageRun:
        """Run exactly one stage of the submission."""
        with self._units() as unit:
            tape = unit.tapes.get(submission_id)
        if tape is None or tape.status not in self._stages:
            status = tape.status if tape is not None else TapeStatus.FAILED
            return StageRun(submission_id, status, StageOutcome.NOTHING_TO_DO, status)
        stage = self._stages[tape.status]
        before = tape.status
        try:
            stage.run(submission_id)
        except StopRequestedError:
            self._log.info(
                LogEvent.TAPE_STAGE_STOPPED, submission_id=str(submission_id), stage=stage.reached.value
            )
            return StageRun(submission_id, before, StageOutcome.STOPPED, before)
        except PipelineStageError as error:
            return self._stage_failed(submission_id, before, error)
        except Exception as error:  # every other error fails the tape with INTERNAL_ERROR (SPEC 13.7)
            self._log.failure(
                LogEvent.STAGE_UNEXPECTED_ERROR,
                error,
                submission_id=str(submission_id),
                stage=stage.reached.value,
            )
            return self._stage_failed(submission_id, before, PipelineStageError())
        self._log.info(
            LogEvent.TAPE_STAGE_ADVANCED, submission_id=str(submission_id), stage=stage.reached.value
        )
        return StageRun(submission_id, before, StageOutcome.ADVANCED, stage.reached)

    def _stage_failed(
        self, submission_id: uuid.UUID, before: TapeStatus, error: PipelineStageError
    ) -> StageRun:
        """Back off or fail; an error that is not a dependency's fails the tape with INTERNAL_ERROR.

        The audit entry, the log record and the returned run carry the same six facts
        (SPEC_DECISIONS 58).
        """
        code, now = error.failure_code, self._clock.now()
        with self._units() as unit:
            tape = unit.tapes.get(submission_id, for_update=True)
            if tape is None or tape.status is not before or tape.next_stage is None:
                return StageRun(submission_id, before, StageOutcome.NOTHING_TO_DO, before)
            stage = tape.next_stage
            if error.transient:
                failed = tape.record_failed_attempt(code, now, self._max_attempts)
            else:
                tape.fail(code, now)
                failed = True
            detail = failure_detail(stage, error, tape.progress.attempts)
            if error.transient:
                unit.record(stage_attempt_failed(submission_id, detail))
            if failed:
                unit.record(tape_failed(submission_id, detail, before))
            unit.tapes.save(tape)
            unit.commit()
        fields = {"submission_id": str(submission_id), **detail}
        if failed:
            self._log.warning(LogEvent.TAPE_FAILED, **fields)
            return StageRun(submission_id, before, StageOutcome.FAILED, TapeStatus.FAILED, detail)
        self._log.warning(LogEvent.TAPE_STAGE_ATTEMPT_FAILED, **fields)
        return StageRun(submission_id, before, StageOutcome.RETRY_SCHEDULED, before, detail)
