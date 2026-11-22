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
    TAPE_STAGE_STOPPED = "tape_stage_stopped"
    TAPE_FAILED = "tape_failed"
    CHECK_CHUNK_COMMITTED = "check_chunk_committed"
    STAGE_UNEXPECTED_ERROR = "stage_unexpected_error"
    DEPENDENCY_RETRY = "dependency_retry"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    ACCESS_DENIED = "access_denied"
    LIBRARY_LOG = "library_log"
    SCHEMA_MIGRATION_APPLIED = "schema_migration_applied"
    CODES_STORED = "codes_stored"
    REGISTRY_REQUESTS_PLANNED = "registry_requests_planned"
    REGISTRY_REQUEST_SENT = "registry_request_sent"
    REGISTRY_RESPONSE_STORED = "registry_response_stored"
    REGISTRY_RESULT_NOT_APPLIED = "registry_result_not_applied"
    CERTIFICATE_STORED = "certificate_stored"
    OUTBOX_SYNCED = "outbox_synced"
    OUTBOX_SYNC_SKIPPED = "outbox_sync_skipped"
    OUTBOX_SYNC_INTERRUPTED = "outbox_sync_interrupted"
    CHECKPOINT_STORED = "checkpoint_stored"
    ALERT_CREATED = "alert_created"
    ALERT_CASE_UPDATED = "alert_case_updated"
    REGISTRY_MESSAGE_IGNORED = "registry_message_ignored"
    HALT_RULE_APPLIED = "halt_rule_applied"
    HALT_RULE_DISABLED = "halt_rule_disabled"
    INTEGRITY_VERIFIED = "integrity_verified"
