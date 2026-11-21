"""Every operational log event name of the bank agent (SPEC 16.5). Fixed lower snake case names."""

from __future__ import annotations

from enum import StrEnum


class LogEvent(StrEnum):
    """The one enumeration of log event names."""

    WORKER_STARTED = "worker_started"
    WORKER_STOPPED = "worker_stopped"
    WORKER_STEP_FAILED = "worker_step_failed"
    TAPE_RECEIVED = "tape_received"
    TAPE_UPLOAD_REJECTED = "tape_upload_rejected"
    TAPE_STAGE_ADVANCED = "tape_stage_advanced"
    TAPE_STAGE_ATTEMPT_FAILED = "tape_stage_attempt_failed"
    TAPE_FAILED = "tape_failed"
    CHECK_CHUNK_COMMITTED = "check_chunk_committed"
    STAGE_UNEXPECTED_ERROR = "stage_unexpected_error"
    DEPENDENCY_RETRY = "dependency_retry"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    ACCESS_DENIED = "access_denied"
    LIBRARY_LOG = "library_log"
