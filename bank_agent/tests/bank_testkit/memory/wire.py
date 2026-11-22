"""The JSON a registry would have sent for a scripted outbox payload, kept as the message's evidence."""

from __future__ import annotations

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.registry_records import (
    AlertNotice,
    CaseNotice,
    MessagePayload,
    QuarantinedPayload,
    SignedCheckpoint,
)


def wire_payload(payload: MessagePayload) -> JsonValue:
    """The JSON a registry would have sent for a scripted payload."""
    if isinstance(payload, AlertNotice):
        return {
            "case_id": str(payload.case_id), "role": payload.role.value, "asset_token": payload.asset_token,
            "match_kind": payload.match_kind.value, "match_basis": payload.match_basis.value,
            "kinds_matched": [kind.value for kind in payload.kinds_matched], "opened_seq": payload.opened_seq,
        }  # fmt: skip
    if isinstance(payload, CaseNotice):
        return {
            "case_id": str(payload.case_id), "asset_token": payload.asset_token,
            "status": payload.status.value, "resolved_seq": payload.resolved_seq,
        }  # fmt: skip
    if isinstance(payload, SignedCheckpoint):
        return {
            "body": dict(payload.body),
            "signature": payload.signature,
            "signing_key_id": payload.signing_key_id,
        }
    if isinstance(payload, QuarantinedPayload):
        return payload.payload
    return None
