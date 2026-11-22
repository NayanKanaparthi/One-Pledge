"""The registry client (`contracts/registry.yaml`): submissions, certificates, outbox, cases, ledger headers.

| Answer | Error |
|---|---|
| unreachable, 429, 5xx (after the caller's retries) | `RegistryUnavailableError` (transient) |
| 409 KEY_VERSION_MISMATCH on a submission | `KeyVersionMismatchError` |
| 409 CASE_ALREADY_RESOLVED, 409 POSITION_ALREADY_RECORDED on a position | the matching named error |
| 401, 403, 409 IDEMPOTENCY_KEY_REUSED, 413, 422, any other 4xx | `RegistryRejectedError` |
| a success outside the contract | `RegistryProtocolError` |

An outbox message of a type the bank does not know is returned without a payload, so
the sync can acknowledge and audit it; a known type with a malformed payload is a
protocol error and stays unacknowledged.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping

import httpx
from pydantic import BaseModel, ValidationError

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.dependency_errors import (
    CaseAlreadyResolvedError,
    KeyVersionMismatchError,
    RegistryProtocolError,
    RegistryRejectedError,
    RegistryUnavailableError,
)
from onepledge_bank_agent.domain.enums import BankPosition
from onepledge_bank_agent.domain.errors import PositionAlreadyRecordedError
from onepledge_bank_agent.domain.registry_records import (
    CaseOutcome,
    EventPage,
    MessagePayload,
    OutboxMessage,
    OutboxPage,
    RegistryAnswer,
    RegistrySubmission,
    SignedCertificate,
)
from onepledge_bank_agent.domain.values import KeyVersion
from onepledge_bank_agent.infrastructure.http.answers import (
    DependencyErrors,
    error_code,
    failure,
    json_object,
    parse,
    plain_json,
)
from onepledge_bank_agent.infrastructure.http.base import JsonBody, RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.registry_wire import (
    AlertMessageBody,
    CaseMessageBody,
    CaseViewBody,
    CheckpointMessageBody,
    EventPageBody,
    MessageEnvelope,
    SignedCertificateBody,
    SubmissionResultBody,
)

SUBMISSIONS = "/v1/submissions"
CERTIFICATE = "/v1/certificates/{certificate_id}"
OUTBOX = "/v1/outbox"
ACK = "/v1/outbox/{message_id}/ack"
RESOLUTION = "/v1/cases/{case_id}/resolution"
EVENTS = "/v1/ledger/events"
SCHEMA_VERSION = 1
CONFLICT = 409
REPLAY_HEADER = "Idempotent-Replay"
ERRORS = DependencyErrors(RegistryUnavailableError, RegistryRejectedError, RegistryProtocolError)


def submission_json(submission: RegistrySubmission) -> JsonBody:
    """The `SubmissionRequest` body."""
    items: list[dict[str, object]] = []
    for item in submission.items:
        entry: dict[str, object] = {"item_ref": str(item.item_ref), "asset_token": item.asset_token}
        if item.field_tokens is not None:
            entry["field_tokens"] = {kind.value: token for kind, token in item.field_tokens.items()}
        items.append(entry)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": submission.kind.value,
        "key_version": submission.key_version,
        "items": items,
    }


def _validate[M: BaseModel](model: type[M], value: object, status: int) -> M:
    try:
        return model.model_validate_json(json.dumps(value))
    except ValidationError:
        raise RegistryProtocolError(status) from None


def _message(value: object, status: int) -> OutboxMessage:
    envelope = _validate(MessageEnvelope, value, status)
    document = json_object(value)
    payload: MessagePayload = None
    if envelope.type == "ALERT":
        payload = _validate(AlertMessageBody, value, status).payload.notice()
    elif envelope.type == "CASE_UPDATE":
        payload = _validate(CaseMessageBody, value, status).payload.notice()
    elif envelope.type == "CHECKPOINT":
        body = _validate(CheckpointMessageBody, value, status)
        payload = body.payload.record(json_object(document.get("payload")))
    return OutboxMessage(envelope.message_id, envelope.position, envelope.type, payload)


class RegistryHttpClient:
    """Implements the `RegistryClient` port."""

    def __init__(self, caller: RetryingHttpCaller) -> None:
        self._caller = caller

    def submit(self, idempotency_key: str, submission: RegistrySubmission) -> RegistryAnswer:
        """`POST /v1/submissions` with the idempotency key."""
        headers = {"Idempotency-Key": idempotency_key}
        response = self._caller.post(SUBMISSIONS, SUBMISSIONS, submission_json(submission), headers)
        conflict = response is not None and response.status_code == CONFLICT
        if conflict and response is not None and error_code(response) == "KEY_VERSION_MISMATCH":
            raise KeyVersionMismatchError(CONFLICT)
        replay = response is not None and response.headers.get(REPLAY_HEADER) == "true"
        if response is None or response.status_code not in (httpx.codes.CREATED, httpx.codes.OK):
            raise failure(response, ERRORS)
        if (response.status_code == httpx.codes.OK) != replay:
            raise RegistryProtocolError(response.status_code)
        body, document = parse(SubmissionResultBody, response, ERRORS)
        return RegistryAnswer(
            status=response.status_code,
            replay=replay,
            submission_id=body.submission_id,
            member_id=body.member_id,
            kind=body.kind,
            key_version=KeyVersion.parse(body.key_version),
            as_of_seq=body.as_of_seq,
            certificate_id=body.certificate_id,
            results=tuple(result.outcome() for result in body.results),
            body=document,
        )

    def certificate(self, certificate_id: uuid.UUID) -> SignedCertificate:
        """`GET /v1/certificates/{certificate_id}`."""
        response = self._caller.get(CERTIFICATE, f"/v1/certificates/{certificate_id}")
        body, document = parse(SignedCertificateBody, self._ok(response), ERRORS)
        return body.record(document)

    def outbox(self, limit: int) -> OutboxPage:
        """`GET /v1/outbox?limit=`."""
        response = self._ok(self._caller.get(OUTBOX, OUTBOX, {"limit": limit}))
        document = self._document(response)
        messages, more = document.get("messages"), document.get("has_more")
        if not isinstance(messages, list) or not isinstance(more, bool):
            raise RegistryProtocolError(response.status_code)
        return OutboxPage(tuple(_message(value, response.status_code) for value in messages), more)

    def acknowledge(self, message_id: uuid.UUID) -> None:
        """`POST /v1/outbox/{message_id}/ack` (204, also for a message already acknowledged)."""
        response = self._caller.post(ACK, f"/v1/outbox/{message_id}/ack")
        if response is None or response.status_code != httpx.codes.NO_CONTENT:
            raise failure(response, ERRORS)

    def record_position(self, case_id: uuid.UUID, position: BankPosition) -> CaseOutcome:
        """`POST /v1/cases/{case_id}/resolution`."""
        response = self._caller.post(
            RESOLUTION, f"/v1/cases/{case_id}/resolution", {"position": position.value}
        )
        if response is not None and response.status_code == CONFLICT:
            code = error_code(response)
            if code == "CASE_ALREADY_RESOLVED":
                raise CaseAlreadyResolvedError(CONFLICT)
            if code == "POSITION_ALREADY_RECORDED":
                raise PositionAlreadyRecordedError
        body, _ = parse(CaseViewBody, self._ok(response), ERRORS)
        return body.outcome()

    def event_headers(self, after_seq: int, limit: int) -> EventPage:
        """`GET /v1/ledger/events?view=headers&after_seq=&limit=`."""
        params: Mapping[str, str | int] = {"view": "headers", "after_seq": after_seq, "limit": limit}
        body, _ = parse(EventPageBody, self._ok(self._caller.get(EVENTS, EVENTS, params)), ERRORS)
        return body.page()

    @staticmethod
    def _ok(response: httpx.Response | None) -> httpx.Response:
        if response is None or response.status_code != httpx.codes.OK:
            raise failure(response, ERRORS)
        return response

    @staticmethod
    def _document(response: httpx.Response) -> Mapping[str, JsonValue]:
        try:
            document = plain_json(response)
        except ValueError:
            raise RegistryProtocolError(response.status_code) from None
        if not isinstance(document, dict):
            raise RegistryProtocolError(response.status_code)
        return json_object(document)
