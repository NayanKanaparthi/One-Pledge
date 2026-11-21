"""`TapeSubmission`: one uploaded tape and its pipeline state machine (SPEC 8.3, 13.7).

The status names the last stage that completed. Only the methods below change it;
the database enforces the same table with a trigger (SPEC 12.4 point 4).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType

from onepledge_bank_agent.domain.enums import FailureCode, TapeKind, TapeSource, TapeStatus
from onepledge_bank_agent.domain.errors import IllegalTransitionError

# The stage that runs from a status, named by the status it reaches.
NEXT_STAGE: Mapping[TapeStatus, TapeStatus] = MappingProxyType(
    {
        TapeStatus.RECEIVED: TapeStatus.CLEANED,
        TapeStatus.CLEANED: TapeStatus.CHECKED,
        TapeStatus.CHECKED: TapeStatus.TOKENIZED,
        TapeStatus.TOKENIZED: TapeStatus.SUBMITTED,
        TapeStatus.SUBMITTED: TapeStatus.COMPLETED,
    }
)
RESUMABLE: frozenset[TapeStatus] = frozenset(NEXT_STAGE)
UNFINISHED: frozenset[TapeStatus] = RESUMABLE
RETRYABLE_FAILURES: frozenset[FailureCode] = frozenset(
    {
        FailureCode.OUTSIDE_RECORDS_UNAVAILABLE,
        FailureCode.KEY_HOLDER_UNAVAILABLE,
        FailureCode.REGISTRY_UNAVAILABLE,
        FailureCode.REGISTRY_INTEGRITY_BROKEN,
        FailureCode.INTERNAL_ERROR,
    }
)
MAX_BACKOFF_SECONDS = 60


@dataclass(frozen=True)
class TapeFacts:
    """The immutable facts of an upload, fixed at intake."""

    submission_id: uuid.UUID
    facility_id: uuid.UUID
    kind: TapeKind
    source: TapeSource
    file_name: str
    file_sha256: str
    rows_sha256: str
    as_of_date: date
    row_count: int
    uploaded_by: uuid.UUID
    uploaded_at: datetime


@dataclass(frozen=True)
class TapeProgress:
    """The mutable part of a submission, as stored."""

    status: TapeStatus
    resume_status: TapeStatus | None
    failed_stage: TapeStatus | None
    failure_code: FailureCode | None
    attempts: int
    next_attempt_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None


class TapeSubmission:
    """One tape submission. Each transition of SPEC 8.3 is one method; anything else raises."""

    def __init__(self, facts: TapeFacts, progress: TapeProgress) -> None:
        self._facts = facts
        self._progress = progress

    @classmethod
    def receive(cls, facts: TapeFacts) -> TapeSubmission:
        """(none) to RECEIVED: intake committed."""
        return cls(
            facts,
            TapeProgress(TapeStatus.RECEIVED, None, None, None, 0, None, facts.uploaded_at, None),
        )

    @property
    def facts(self) -> TapeFacts:
        """The immutable facts of the upload."""
        return self._facts

    @property
    def progress(self) -> TapeProgress:
        """Status and failure handling fields."""
        return self._progress

    @property
    def submission_id(self) -> uuid.UUID:
        """The submission id."""
        return self._facts.submission_id

    @property
    def status(self) -> TapeStatus:
        """The last completed stage, or FAILED."""
        return self._progress.status

    @property
    def next_stage(self) -> TapeStatus | None:
        """The status the next stage reaches; None when COMPLETED or FAILED."""
        return NEXT_STAGE.get(self._progress.status)

    def mark_cleaned(self, at: datetime) -> TapeStatus:
        """RECEIVED to CLEANED: the clean stage committed. Returns the old status."""
        return self._advance(TapeStatus.CLEANED, at)

    def mark_checked(self, at: datetime) -> TapeStatus:
        """CLEANED to CHECKED: the check stage committed."""
        return self._advance(TapeStatus.CHECKED, at)

    def mark_tokenized(self, at: datetime) -> TapeStatus:
        """CHECKED to TOKENIZED: the tokenise stage committed."""
        return self._advance(TapeStatus.TOKENIZED, at)

    def mark_submitted(self, at: datetime) -> TapeStatus:
        """TOKENIZED to SUBMITTED: every planned registry request has a stored response."""
        return self._advance(TapeStatus.SUBMITTED, at)

    def mark_completed(self, at: datetime) -> TapeStatus:
        """SUBMITTED to COMPLETED: every certificate fetched, verified and stored."""
        return self._advance(TapeStatus.COMPLETED, at)

    def _advance(self, reached: TapeStatus, at: datetime) -> TapeStatus:
        before = self._progress.status
        if NEXT_STAGE.get(before) is not reached:
            raise IllegalTransitionError("tape_submission", before.value, reached.value)
        self._progress = TapeProgress(
            reached, None, None, None, 0, None, at, at if reached is TapeStatus.COMPLETED else None
        )
        return before

    def record_failed_attempt(self, code: FailureCode, at: datetime, max_attempts: int) -> bool:
        """A transient failure of the next stage. Backs off, or fails at `max_attempts`. True when FAILED."""
        self._require_next_stage()
        attempts = self._progress.attempts + 1
        if attempts >= max_attempts:
            self.fail(code, at, attempts)
            return True
        delay = timedelta(seconds=min(2**attempts, MAX_BACKOFF_SECONDS))
        self._progress = TapeProgress(self._progress.status, None, None, None, attempts, at + delay, at, None)
        return False

    def fail(self, code: FailureCode, at: datetime, attempts: int | None = None) -> None:
        """Any unfinished status to FAILED, keeping the status to resume from and the stage attempted."""
        stage = self._require_next_stage()
        before = self._progress.status
        self._progress = TapeProgress(
            TapeStatus.FAILED,
            before,
            stage,
            code,
            self._progress.attempts if attempts is None else attempts,
            None,
            at,
            None,
        )

    def resume(self, at: datetime) -> TapeStatus:
        """FAILED to its resume status when the failure code is retryable (SPEC 13.8)."""
        progress = self._progress
        if progress.status is not TapeStatus.FAILED or progress.resume_status is None:
            raise IllegalTransitionError("tape_submission", progress.status.value, "RESUME")
        if progress.failure_code not in RETRYABLE_FAILURES:
            raise IllegalTransitionError(
                "tape_submission", progress.status.value, progress.resume_status.value
            )
        self._progress = TapeProgress(progress.resume_status, None, None, None, 0, None, at, None)
        return progress.resume_status

    def _require_next_stage(self) -> TapeStatus:
        stage = NEXT_STAGE.get(self._progress.status)
        if stage is None:
            raise IllegalTransitionError(
                "tape_submission", self._progress.status.value, TapeStatus.FAILED.value
            )
        return stage
