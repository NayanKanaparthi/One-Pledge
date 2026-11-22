"""Builders of the audit entries of the registry side (SPEC 17.2). Identifiers, codes and counts only.

No entry holds a loan value, a token input, a note typed by a user, or anything the
registry did not already see: asset codes are not written either, only the bank's own
ids and the registry's ids.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping

from onepledge_bank_agent.domain.audit import Actor, AuditDraft
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.entities import Checkpoint, RegistryRequest
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, AuditOutcome
from onepledge_bank_agent.domain.registry_records import QuarantinedMessage, RegistryAnswer

SYSTEM = Actor.system()
CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
UNREADABLE = "UNREADABLE"


def entry(
    action: AuditAction,
    object_type: AuditObjectType,
    object_id: uuid.UUID | None,
    detail: Mapping[str, JsonValue],
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
) -> AuditDraft:
    """One audit draft of the system actor."""
    return AuditDraft(action, SYSTEM, object_type, object_id, outcome, dict(detail))


def request_sent(request: RegistryRequest, attempts: int) -> AuditDraft:
    """`REGISTRY_REQUEST_SENT`: which request, which attempt, with which key."""
    return entry(
        AuditAction.REGISTRY_REQUEST_SENT,
        AuditObjectType.REGISTRY_REQUEST,
        request.request_id,
        {
            "submission_id": str(request.submission_id),
            "origin": request.origin.value,
            "kind": request.kind.value,
            "batch_index": request.batch_index,
            "item_count": request.item_count,
            "attempts": attempts,
            "idempotency_key": request.idempotency_key.value,
        },
    )


def response_received(
    request: RegistryRequest, answer: RegistryAnswer, results: Mapping[str, int]
) -> AuditDraft:
    """`REGISTRY_RESPONSE_RECEIVED`: the status, whether it was a replay, the ledger position, counts."""
    return entry(
        AuditAction.REGISTRY_RESPONSE_RECEIVED,
        AuditObjectType.REGISTRY_REQUEST,
        request.request_id,
        {
            "submission_id": str(request.submission_id),
            "response_status": answer.status,
            "replay": answer.replay,
            "registry_submission_id": str(answer.submission_id),
            "certificate_id": str(answer.certificate_id),
            "as_of_seq": answer.as_of_seq,
            "results": dict(results),
        },
    )


def item_rejected(request_id: uuid.UUID, loan_id: uuid.UUID, reason: str) -> AuditDraft:
    """`REGISTRY_ITEM_REJECTED` (FAILURE): the loan and the registry's reason code."""
    return entry(
        AuditAction.REGISTRY_ITEM_REJECTED,
        AuditObjectType.REGISTRY_REQUEST,
        request_id,
        {"loan_id": str(loan_id), "reason": reason},
        AuditOutcome.FAILURE,
    )


def certificate_checked(
    certificate_id: uuid.UUID, request: RegistryRequest, failure: str | None, as_of_seq: int
) -> AuditDraft:
    """`CERTIFICATE_VERIFIED`, or `CERTIFICATE_REJECTED` (FAILURE) with the reason."""
    detail: dict[str, JsonValue] = {
        "request_id": str(request.request_id),
        "submission_id": str(request.submission_id),
        "as_of_seq": as_of_seq,
    }
    if failure is None:
        return entry(AuditAction.CERTIFICATE_VERIFIED, AuditObjectType.CERTIFICATE, certificate_id, detail)
    return entry(
        AuditAction.CERTIFICATE_REJECTED,
        AuditObjectType.CERTIFICATE,
        certificate_id,
        {**detail, "failure_reason": failure},
        AuditOutcome.FAILURE,
    )


def checkpoint_checked(checkpoint: Checkpoint) -> AuditDraft:
    """`CHECKPOINT_VERIFIED`, or `CHECKPOINT_REJECTED` (FAILURE) with the reason and the first bad seq."""
    detail: dict[str, JsonValue] = {
        "checkpoint_seq": checkpoint.checkpoint_seq,
        "last_event_seq": checkpoint.last_event_seq,
        "head_hash": checkpoint.head_hash.value,
        "signing_key_id": checkpoint.signing_key_id,
    }
    if checkpoint.failure is None:
        return entry(
            AuditAction.CHECKPOINT_VERIFIED, AuditObjectType.CHECKPOINT, checkpoint.checkpoint_id, detail
        )
    return entry(
        AuditAction.CHECKPOINT_REJECTED,
        AuditObjectType.CHECKPOINT,
        checkpoint.checkpoint_id,
        {
            **detail,
            "failure_reason": checkpoint.failure.value,
            "first_bad_event_seq": checkpoint.first_bad_event_seq,
        },
        AuditOutcome.FAILURE,
    )


def message_ignored(message_id: uuid.UUID, message_type: str) -> AuditDraft:
    """`REGISTRY_MESSAGE_IGNORED`: an outbox message of a type this bank does not know."""
    return entry(
        AuditAction.REGISTRY_MESSAGE_IGNORED,
        AuditObjectType.REGISTRY_MESSAGE,
        message_id,
        {"message_type": message_type if CODE.fullmatch(message_type) else UNREADABLE},
    )


def message_quarantined(row: QuarantinedMessage, sqlstate: str | None = None) -> AuditDraft:
    """`REGISTRY_MESSAGE_QUARANTINED` (SPEC_DECISIONS 85): the message type, the reason, the payload hash,
    the SQLSTATE when the store refused it; the object is the quarantine row (decision 89)."""
    detail: dict[str, JsonValue] = {
        "message_id": str(row.message_id),
        "message_type": row.message_type if CODE.fullmatch(row.message_type) else UNREADABLE,
        "reason": row.quarantined.reason.value,
        "payload_sha256": row.quarantined.payload_sha256,
    }
    if sqlstate is not None:
        detail["sqlstate"] = sqlstate
    return entry(
        AuditAction.REGISTRY_MESSAGE_QUARANTINED,
        AuditObjectType.REGISTRY_MESSAGE,
        row.quarantine_id,
        detail,
        AuditOutcome.FAILURE,
    )
