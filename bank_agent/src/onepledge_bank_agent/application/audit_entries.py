"""Builders of the audit entries the pipeline stages write (SPEC 17.2). Ids, codes and counts only."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

from onepledge_bank_agent.domain.audit import Actor, AuditDraft
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    AuditObjectType,
    AuditOutcome,
    FailureCode,
    TapeStatus,
)
from onepledge_bank_agent.domain.loan import StateChange


def loan_state_changed(
    change: StateChange, submission_id: uuid.UUID, reason_code: str, failed_checks: Sequence[str] = ()
) -> AuditDraft:
    """`LOAN_STATE_CHANGED` with from, to, the submission and the reason."""
    detail: dict[str, JsonValue] = {
        "from": change.from_state.value if change.from_state else None,
        "to": change.to_state.value,
        "submission_id": str(submission_id),
        "reason_code": reason_code,
    }
    if failed_checks:
        detail["failed_checks"] = list(failed_checks)
    return AuditDraft(
        AuditAction.LOAN_STATE_CHANGED, Actor.system(), AuditObjectType.LOAN, change.loan_id, detail=detail
    )


def stage_advanced(
    submission_id: uuid.UUID, before: TapeStatus, after: TapeStatus, counts: Mapping[str, int]
) -> AuditDraft:
    """`TAPE_STAGE_ADVANCED` with from, to and the counts of that stage."""
    detail: dict[str, JsonValue] = {"from": before.value, "to": after.value, **counts}
    return AuditDraft(
        AuditAction.TAPE_STAGE_ADVANCED,
        Actor.system(),
        AuditObjectType.TAPE_SUBMISSION,
        submission_id,
        detail=detail,
    )


def stage_attempt_failed(submission_id: uuid.UUID, stage: TapeStatus, code: str, attempts: int) -> AuditDraft:
    """`TAPE_STAGE_ATTEMPT_FAILED` with the stage attempted, the error code and the attempt count."""
    return AuditDraft(
        AuditAction.TAPE_STAGE_ATTEMPT_FAILED,
        Actor.system(),
        AuditObjectType.TAPE_SUBMISSION,
        submission_id,
        AuditOutcome.FAILURE,
        {"stage": stage.value, "error_code": code, "attempts": attempts},
    )


def tape_failed(
    submission_id: uuid.UUID,
    stage: TapeStatus,
    resume_status: TapeStatus,
    code: FailureCode,
    dependency: Mapping[str, str | int | None],
) -> AuditDraft:
    """`TAPE_FAILED` with the stage attempted, the status to resume from, the failure code and, when a
    dependency failed, `dependency`, `http_status` and (for a refusal) `reason` (SPEC_DECISIONS 26)."""
    return AuditDraft(
        AuditAction.TAPE_FAILED,
        Actor.system(),
        AuditObjectType.TAPE_SUBMISSION,
        submission_id,
        AuditOutcome.FAILURE,
        {
            "stage": stage.value,
            "resume_status": resume_status.value,
            "failure_code": code.value,
            **dependency,
        },
    )
