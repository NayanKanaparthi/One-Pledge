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
    TapeStatus,
)
from onepledge_bank_agent.domain.errors import PipelineStageError
from onepledge_bank_agent.domain.loan import StateChange


def loan_state_changed(
    change: StateChange,
    submission_id: uuid.UUID,
    reason_code: str,
    failed_checks: Sequence[str] | None = None,
) -> AuditDraft:
    """`LOAN_STATE_CHANGED` with from, to, the submission and the reason."""
    detail: dict[str, JsonValue] = {
        "from": change.from_state.value if change.from_state else None,
        "to": change.to_state.value,
        "submission_id": str(submission_id),
        "reason_code": reason_code,
    }
    if failed_checks is not None:
        detail["failed_checks"] = list(failed_checks)
    return AuditDraft(
        AuditAction.LOAN_STATE_CHANGED, Actor.system(), AuditObjectType.LOAN, change.loan_id, detail=detail
    )


def stage_advanced(
    submission_id: uuid.UUID, before: TapeStatus, after: TapeStatus, counts: Mapping[str, int | str]
) -> AuditDraft:
    """`TAPE_STAGE_ADVANCED` with from, to, the counts of that stage and the digests of what it produced
    (SPEC_DECISIONS 107 a)."""
    detail: dict[str, JsonValue] = {"from": before.value, "to": after.value, **counts}
    return AuditDraft(
        AuditAction.TAPE_STAGE_ADVANCED,
        Actor.system(),
        AuditObjectType.TAPE_SUBMISSION,
        submission_id,
        detail=detail,
    )


type FailureDetail = dict[str, str | int | None]


def failure_detail(stage: TapeStatus, error: PipelineStageError, attempts: int) -> FailureDetail:
    """The six facts of a stage failure, always all present (SPEC_DECISIONS 58)."""
    return {"stage": stage.value, "failure_code": error.failure_code.value, "attempts": attempts,
            **error.failure_facts()}  # fmt: skip


def stage_attempt_failed(submission_id: uuid.UUID, detail: FailureDetail) -> AuditDraft:
    """`TAPE_STAGE_ATTEMPT_FAILED` with the six failure facts."""
    return AuditDraft(
        AuditAction.TAPE_STAGE_ATTEMPT_FAILED,
        Actor.system(),
        AuditObjectType.TAPE_SUBMISSION,
        submission_id,
        AuditOutcome.FAILURE,
        dict(detail),
    )


def tape_failed(
    submission_id: uuid.UUID, detail: Mapping[str, JsonValue], resume_status: TapeStatus
) -> AuditDraft:
    """`TAPE_FAILED` with the six failure facts and the status to resume from."""
    return AuditDraft(
        AuditAction.TAPE_FAILED,
        Actor.system(),
        AuditObjectType.TAPE_SUBMISSION,
        submission_id,
        AuditOutcome.FAILURE,
        {**detail, "resume_status": resume_status.value},
    )
