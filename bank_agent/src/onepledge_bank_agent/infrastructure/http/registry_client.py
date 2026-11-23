"""The registry client (`contracts/registry.yaml`): submissions, certificates, outbox, cases, ledger headers.

| Answer | Error |
|---|---|
| unreachable, 429, 5xx (after the caller's retries) | `RegistryUnavailableError` (transient) |
| 409 KEY_VERSION_MISMATCH on a submission | `KeyVersionMismatchError` |
| 409 CASE_ALREADY_RESOLVED, 409 POSITION_ALREADY_RECORDED on a position | the matching named error |
| 401, 403, 409 IDEMPOTENCY_KEY_REUSED, 413, 422, any other 4xx | `RegistryRejectedError` |
| a success outside the contract | `RegistryProtocolError` |

An outbox message of a type the bank does not know is returned without a payload, so
the sync can acknowledge and audit it. A known type is validated against its whole
schema (types, patterns, `uniqueItems`, required fields) and every number must fit the
column it is stored in (SPEC_DECISIONS 85, rule 1); a message that fails is returned as a
`QuarantinedPayload` with reason OVERSIZED (over 64 KiB), VALUE_OUT_OF_RANGE (only a
number too large) or SCHEMA_MISMATCH, so the sync keeps it as evidence and the outbox is
never blocked. A number with a fraction or an exponent, or an integer literal of more
than 4000 digits, is kept as its text. An id or a base64 value that is not spelled
canonically, or an object that holds a key twice, fails the schema (SPEC_DECISIONS 74, 75),
for a message of an unknown type too. A known message holding a value the bank cannot store
and read back (`storable_json`, SPEC_DECISIONS 100: a control character, a surrogate, text
over 255 characters, an integer outside `bigint`, a timestamp outside 2000 to 2100) is
VALUE_OUT_OF_RANGE, and a payload holding one is kept as its hash alone. Only a message
without a parseable id or a usable type, or a page that is not one bounded JSON document
(`bounded_json`), is a protocol error of the page.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from types import MappingProxyType

import httpx
from pydantic import BaseModel, ValidationError

from onepledge_bank_agent.domain.canonical_json import JsonValue, canonical_json, sha256_hex
from onepledge_bank_agent.domain.dependency_errors import (
    REGISTRY,
    CaseAlreadyResolvedError,
    KeyVersionMismatchError,
    RegistryProtocolError,
)
from onepledge_bank_agent.domain.enums import BankPosition, QuarantineReason
from onepledge_bank_agent.domain.errors import PositionAlreadyRecordedError
from onepledge_bank_agent.domain.registry_records import (
    CaseOutcome,
    EventPage,
    MessageEvidence,
    MessagePayload,
    OutboxElement,
    OutboxMessage,
    OutboxPage,
    QuarantinedPayload,
    RegistryAnswer,
    RegistrySubmission,
    SignedCertificate,
    UnacknowledgeableElement,
)
from onepledge_bank_agent.domain.storable import storable_json
from onepledge_bank_agent.domain.values import KeyVersion
from onepledge_bank_agent.infrastructure import bounded_json
from onepledge_bank_agent.infrastructure.encodings import canonical_uuid
from onepledge_bank_agent.infrastructure.http import classification
from onepledge_bank_agent.infrastructure.http.answers import error_code, json_object, parse, plain_json
from onepledge_bank_agent.infrastructure.http.base import JsonBody, OutboundRequest, RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.classification import failure
from onepledge_bank_agent.infrastructure.http.registry_wire import (
    BIGINT_MAX,
    AlertMessageBody,
    CaseMessageBody,
    CaseViewBody,
    CheckpointMessageBody,
    EventPageBody,
    OutOfStorableRangeError,
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
ERRORS = classification.ERRORS[REGISTRY]


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


class _FloatText(str):
    """A JSON number with a fraction or an exponent, kept as its text (the contract has none)."""


class _LongIntText(str):
    """An integer literal too long to convert, kept as its text; it stands for a value out of range."""


MAX_INT_DIGITS = 4000


def _integer(text: str) -> int | str:
    return int(text) if len(text) <= MAX_INT_DIGITS else _LongIntText(text)


KNOWN_MESSAGES: Mapping[str, type[BaseModel]] = MappingProxyType(
    {"ALERT": AlertMessageBody, "CASE_UPDATE": CaseMessageBody, "CHECKPOINT": CheckpointMessageBody}
)
MAX_QUARANTINED_PAYLOAD_BYTES = 64 * 1024
OUT_OF_RANGE = frozenset({"less_than_equal"})
SCHEMA_MISMATCH = QuarantineReason.SCHEMA_MISMATCH
MESSAGE_TYPE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
UNREADABLE_TYPE = "UNREADABLE"
"""The type recorded for a message whose own type is not a string of `MESSAGE_TYPE`."""


def _validation_copy(value: object) -> object:
    """The message as validated: an over long integer becomes one past the largest storable value."""
    if isinstance(value, _LongIntText):
        return BIGINT_MAX + 1
    if isinstance(value, dict):
        return {key: _validation_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_validation_copy(item) for item in value]
    return value


def _serialised(payload: JsonValue, unstorable: bool) -> bytes:
    """The form a payload is hashed in: its canonical JSON, or, when it holds text the bank cannot store,
    its canonical JSON with every character outside ASCII escaped (a surrogate has no UTF-8 form)."""
    if not unstorable:
        return canonical_json(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _evidence(payload: JsonValue) -> MessageEvidence:
    """The payload as received and its hash; only the hash when it exceeds 64 KiB or holds text the bank
    cannot store."""
    unstorable = not storable_json(payload)
    serialised = _serialised(payload, unstorable)
    oversized = len(serialised) > MAX_QUARANTINED_PAYLOAD_BYTES
    withheld = oversized or unstorable
    return MessageEvidence(None if withheld else payload, sha256_hex(serialised), oversized, withheld)


def _rejection(model: type[BaseModel], value: object, evidence: MessageEvidence) -> QuarantineReason | None:
    """None when the message matches its contract in full and every number fits its column."""
    if evidence.oversized:
        return QuarantineReason.OVERSIZED
    if not storable_json(value):  # SPEC_DECISIONS 100: a value the bank cannot store and read back
        return QuarantineReason.VALUE_OUT_OF_RANGE
    try:
        model.model_validate_json(json.dumps(_validation_copy(value)))
    except ValidationError as error:
        out_of_range = all(_out_of_range(detail) for detail in error.errors())
        return QuarantineReason.VALUE_OUT_OF_RANGE if out_of_range else QuarantineReason.SCHEMA_MISMATCH
    return None


def _out_of_range(detail: Mapping[str, object]) -> bool:
    """A number past its column's bound, or a well formed timestamp the bank cannot hold (decision 100)."""
    if detail.get("type") in OUT_OF_RANGE:
        return True
    context = detail.get("ctx")
    return isinstance(context, dict) and isinstance(context.get("error"), OutOfStorableRangeError)


def _position(value: object) -> int | None:
    """The position when it is a storable one; anything else is kept as unknown."""
    position = json_object(value).get("position")
    if isinstance(position, int) and not isinstance(position, bool) and 1 <= position <= BIGINT_MAX:
        return position
    return None


def _message_id(document: Mapping[str, JsonValue]) -> uuid.UUID | None:
    """The message id when it is spelled canonically (SPEC_DECISIONS 75); anything else is no usable id."""
    text = document.get("message_id")
    return canonical_uuid(text) if isinstance(text, str) else None


def _type(document: Mapping[str, JsonValue]) -> str | None:
    """The message type when it is a string of the type pattern of SPEC_DECISIONS 101."""
    text = document.get("type")
    return text if isinstance(text, str) and MESSAGE_TYPE.fullmatch(text) else None


def outbox_element(value: JsonValue, status: int) -> OutboxElement:
    """One element of an outbox page, by the table of SPEC_DECISIONS 101 level 2.

    No object or no canonical id: unacknowledgeable, kept by its hash. A usable id and a type of
    the pattern that the bank does not know: ignored (payload None). A usable id and anything
    else wrong (the type, the position, the time, the payload against the contract or against
    decision 100): poison, with the type recorded as UNREADABLE when it is not of the pattern.
    """
    message_id = _message_id(value) if isinstance(value, dict) else None
    if message_id is None:
        return UnacknowledgeableElement(_evidence(json_object(value) if isinstance(value, dict) else value))
    document = json_object(value)
    evidence = _evidence(document.get("payload"))
    position = _position(value)
    message_type = _type(document)
    model = KNOWN_MESSAGES.get(message_type) if message_type is not None else None
    if message_type is not None and model is None:
        return OutboxMessage(message_id, position, message_type, None, evidence)
    reason = (
        _rejection(model, value, evidence)
        if model is not None and not bounded_json.holds_duplicate_key(value)
        else SCHEMA_MISMATCH
    )
    stored_type = message_type or UNREADABLE_TYPE
    if reason is not None:
        quarantined = QuarantinedPayload(reason, evidence.payload, evidence.payload_sha256, evidence.withheld)
        return OutboxMessage(message_id, position, stored_type, quarantined, evidence)
    payload: MessagePayload
    if stored_type == "ALERT":
        payload = _validate(AlertMessageBody, value, status).payload.notice()
    elif stored_type == "CASE_UPDATE":
        payload = _validate(CaseMessageBody, value, status).payload.notice()
    else:
        body = _validate(CheckpointMessageBody, value, status)
        payload = body.payload.record(json_object(document.get("payload")))
    return OutboxMessage(message_id, position, stored_type, payload, evidence)


class RegistryHttpClient:
    """Implements the `RegistryClient` port."""

    def __init__(self, caller: RetryingHttpCaller) -> None:
        self._caller = caller

    def submit(
        self, idempotency_key: str, submission: RegistrySubmission, before_each: Callable[[], None]
    ) -> RegistryAnswer:
        """`POST /v1/submissions` with the idempotency key; `before_each` runs before every attempt."""
        headers = {"Idempotency-Key": idempotency_key}
        outbound = OutboundRequest(
            "POST", SUBMISSIONS, SUBMISSIONS, body=submission_json(submission), headers=headers,
            before_each=before_each,
        )  # fmt: skip
        response = self._caller.send(outbound)
        conflict = response.status_code == CONFLICT
        if conflict and error_code(response) == "KEY_VERSION_MISMATCH":
            raise KeyVersionMismatchError(CONFLICT)
        replay = response.headers.get(REPLAY_HEADER) == "true"
        if response.status_code not in (httpx.codes.CREATED, httpx.codes.OK):
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
        try:
            page = bounded_json.loads(
                response.content,
                max_number_length=None,
                mark_duplicates=True,
                parse_float=_FloatText,
                parse_int=_integer,
            )
        except ValueError:
            raise RegistryProtocolError(response.status_code) from None
        if not isinstance(page, dict) or isinstance(page, bounded_json.DuplicateKeyObject):
            raise RegistryProtocolError(response.status_code)
        messages, more = page.get("messages"), page.get("has_more")  # as read: duplicate keys stay marked
        if not isinstance(messages, list) or not isinstance(more, bool):
            raise RegistryProtocolError(response.status_code)
        return OutboxPage(tuple(outbox_element(value, response.status_code) for value in messages), more)

    def acknowledge(self, message_id: uuid.UUID) -> None:
        """`POST /v1/outbox/{message_id}/ack` (204, also for a message already acknowledged)."""
        response = self._caller.post(ACK, f"/v1/outbox/{message_id}/ack")
        if response.status_code != httpx.codes.NO_CONTENT:
            raise failure(response, ERRORS)

    def record_position(self, case_id: uuid.UUID, position: BankPosition) -> CaseOutcome:
        """`POST /v1/cases/{case_id}/resolution`."""
        response = self._caller.post(
            RESOLUTION, f"/v1/cases/{case_id}/resolution", {"position": position.value}
        )
        if response.status_code == CONFLICT:
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
    def _ok(response: httpx.Response) -> httpx.Response:
        if response.status_code != httpx.codes.OK:
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
