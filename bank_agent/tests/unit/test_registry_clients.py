"""The key holder and registry clients over a mocked network boundary (SPEC 13.3, 13.4, 16.6).

Answers come from scripted handlers. Every error is named with its dependency, HTTP status
and reason; a malformed answer, a float in a signed body or a replay header that disagrees
with the status is a protocol error; unknown fields are ignored; every send is counted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid

import httpx
import pytest

from bank_testkit.memory.clocks import CountingIds
from bank_testkit.scripted_http import Handler, caller, registry
from onepledge_bank_agent.domain.canonical_json import canonical_json
from onepledge_bank_agent.domain.dependency_errors import (
    CaseAlreadyResolvedError,
    KeyHolderProtocolError,
    KeyHolderRejectedError,
    KeyHolderUnavailableError,
    KeyVersionMismatchError,
    RegistryProtocolError,
    RegistryRejectedError,
    RegistryUnavailableError,
)
from onepledge_bank_agent.domain.enums import BankPosition, CaseStatus, FailureReason, TapeKind, TokenKind
from onepledge_bank_agent.domain.errors import PipelineStageError, PositionAlreadyRecordedError
from onepledge_bank_agent.domain.registry_records import QuarantinedPayload, RegistryItem, RegistrySubmission
from onepledge_bank_agent.domain.storable import storable_json
from onepledge_bank_agent.infrastructure.http.key_holder_client import KeyHolderHttpClient

pytestmark = pytest.mark.unit

POINT = base64.b64encode(bytes(range(1, 33))).decode()
ITEM_REF, TOKEN = uuid.UUID(int=7, version=4), "a" * 64


def holder(handler: Handler, seen: list[httpx.Request] | None = None) -> KeyHolderHttpClient:
    return KeyHolderHttpClient(
        caller(handler, "key_holder_2", seen if seen is not None else []), "key_holder_2", CountingIds()
    )


def evaluated(request: httpx.Request, **changes: object) -> httpx.Response:
    sent = json.loads(request.content)
    body = {
        "request_id": sent["request_id"],
        "holder_id": "kh2",
        "key_version": 1,
        "elements": [POINT] * len(sent["elements"]),
    }
    return httpx.Response(200, json={**body, **changes})


def test_a_key_holder_answer_keeps_the_order_and_the_request_pins_no_key_version() -> None:
    seen: list[httpx.Request] = []
    answer = holder(evaluated, seen).evaluate([POINT, POINT])
    assert answer.elements == (POINT, POINT) and answer.key_version == 1
    sent = json.loads(seen[0].content)
    assert sent["key_version"] is None and sent["elements"] == [POINT, POINT]
    assert seen[0].headers["X-API-Key"] == "k" * 40 and seen[0].url.path == "/v1/evaluate"


MALFORMED = FailureReason.MALFORMED_RESPONSE
VIOLATION = FailureReason.PROTOCOL_VIOLATION
ZERO_POINT = base64.b64encode(bytes(32)).decode()
KEY_HOLDER_FAILURES: list[tuple[Handler, type[PipelineStageError], int, FailureReason]] = [
    (lambda r: httpx.Response(503), KeyHolderUnavailableError, 503, FailureReason.SERVER_ERROR),
    (lambda r: httpx.Response(429), KeyHolderUnavailableError, 429, FailureReason.RATE_LIMITED),
    (lambda r: httpx.Response(401), KeyHolderRejectedError, 401, FailureReason.UNAUTHENTICATED),
    (lambda r: httpx.Response(422), KeyHolderRejectedError, 422, FailureReason.REQUEST_REFUSED),
    (lambda r: evaluated(r, elements=[POINT]), KeyHolderProtocolError, 200, VIOLATION),
    (lambda r: evaluated(r, request_id=str(uuid.uuid4())), KeyHolderProtocolError, 200, VIOLATION),
    (lambda r: evaluated(r, elements=[ZERO_POINT] * 2), KeyHolderProtocolError, 200, VIOLATION),
    (lambda r: evaluated(r, key_version=1.0), KeyHolderRejectedError, 200, MALFORMED),
    (lambda r: httpx.Response(200, content=b"not json"), KeyHolderRejectedError, 200, MALFORMED),
]


@pytest.mark.parametrize(("handler", "error", "status", "reason"), KEY_HOLDER_FAILURES)
def test_key_holder_failures_are_named_with_dependency_status_and_reason(
    handler: Handler, error: type[PipelineStageError], status: int, reason: FailureReason
) -> None:
    with pytest.raises(error) as raised:
        holder(handler).evaluate([POINT, POINT])
    assert type(raised.value) is error
    assert raised.value.failure_facts() == {
        "dependency": "key_holder_2",
        "http_status": status,
        "reason": reason.value,
    }


def test_an_unreachable_key_holder_is_transient_with_no_status() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(KeyHolderUnavailableError) as raised:
        holder(down).evaluate([POINT])
    assert raised.value.transient and raised.value.http_status is None


SUBMISSION = RegistrySubmission(
    TapeKind.PLEDGE, "1.1", (RegistryItem(ITEM_REF, TOKEN, {TokenKind.VIN: "b" * 64}),)
)


def decided(
    request: httpx.Request, status: int = 201, replay: bool = False, **changes: object
) -> httpx.Response:
    result = {
        "item_ref": str(ITEM_REF),
        "asset_token": TOKEN,
        "result": "LOCKED",
        "lock_id": str(uuid.uuid4()),
        "already_held": False,
        "match_kind": None,
        "match_basis": None,
        "kinds_matched": [],
        "case_id": None,
        "reason": None,
    }
    body = {
        "submission_id": str(uuid.uuid4()),
        "member_id": str(uuid.uuid4()),
        "kind": "PLEDGE",
        "key_version": "1.1",
        "status": "COMPLETED",
        "received_at": "2026-11-20T12:00:00Z",
        "as_of_seq": 9,
        "certificate_id": str(uuid.uuid4()),
        "future_field": {"ignored": True},
        "results": [result],
    }
    headers = {"Idempotent-Replay": "true"} if replay else {}
    return httpx.Response(status, json={**body, **changes}, headers=headers)


def test_a_decision_and_a_replay_are_read_and_every_attempt_is_counted() -> None:
    seen: list[httpx.Request] = []
    counted: list[int] = []
    answers = iter([httpx.Response(503), httpx.Response(503)])

    def flaky(request: httpx.Request) -> httpx.Response:
        return next(answers, None) or decided(request, 200, replay=True)

    answer = registry(flaky, seen).submit("f" * 64, SUBMISSION, lambda: counted.append(1))
    assert (answer.status, answer.replay, len(counted)) == (200, True, 3)
    assert {request.headers["Idempotency-Key"] for request in seen} == {"f" * 64}
    assert len({request.content for request in seen}) == 1, "every attempt sends the same body"
    body = json.loads(seen[0].content)
    assert body["items"] == [
        {"item_ref": str(ITEM_REF), "asset_token": TOKEN, "field_tokens": {"VIN": "b" * 64}}
    ]
    assert registry(decided).submit("f" * 64, SUBMISSION, lambda: None).results[0].result == "LOCKED"


def refused(code: str) -> httpx.Response:
    return httpx.Response(409, json={"error": {"code": code, "message": "refused"}})


SUBMISSION_FAILURES: list[tuple[Handler, type[Exception]]] = [
    (lambda r: decided(r, 200), RegistryProtocolError),
    (lambda r: decided(r, 201, replay=True), RegistryProtocolError),
    (lambda r: decided(r, as_of_seq=9.0), RegistryRejectedError),
    (lambda r: decided(r, results="LOCKED"), RegistryRejectedError),
    (lambda r: refused("KEY_VERSION_MISMATCH"), KeyVersionMismatchError),
    (lambda r: refused("IDEMPOTENCY_KEY_REUSED"), RegistryRejectedError),
    (lambda r: httpx.Response(413), RegistryRejectedError),
    (lambda r: httpx.Response(504), RegistryUnavailableError),
]


@pytest.mark.parametrize(("handler", "error"), SUBMISSION_FAILURES)
def test_registry_submission_failures_are_named(handler: Handler, error: type[Exception]) -> None:
    with pytest.raises(error):
        registry(handler).submit("f" * 64, SUBMISSION, lambda: None)


def test_case_positions_map_the_registry_refusals_and_read_the_new_status() -> None:
    def resolved(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"position": "DATA_ERROR"}
        return httpx.Response(200, json={
            "case_id": str(uuid.uuid4()), "status": "CLEARED_DATA_ERROR", "your_position": "DATA_ERROR",
            "awaiting_counterparty": False, "resolved_seq": 12,
        })  # fmt: skip

    outcome = registry(resolved).record_position(uuid.uuid4(), BankPosition.DATA_ERROR)
    assert outcome.status is CaseStatus.CLEARED_DATA_ERROR
    with pytest.raises(CaseAlreadyResolvedError):
        registry(lambda r: refused("CASE_ALREADY_RESOLVED")).record_position(
            uuid.uuid4(), BankPosition.DATA_ERROR
        )
    with pytest.raises(PositionAlreadyRecordedError):
        registry(lambda r: refused("POSITION_ALREADY_RECORDED")).record_position(
            uuid.uuid4(), BankPosition.DATA_ERROR
        )


def test_an_acknowledgement_must_be_204_and_an_outbox_page_must_be_well_formed() -> None:
    registry(lambda r: httpx.Response(204)).acknowledge(uuid.uuid4())
    with pytest.raises(RegistryRejectedError):
        registry(lambda r: httpx.Response(404)).acknowledge(uuid.uuid4())
    with pytest.raises(RegistryProtocolError):
        registry(lambda r: httpx.Response(200, json={"messages": {}, "has_more": False})).outbox(100)
    unknown = {
        "message_id": str(uuid.uuid4()),
        "position": 1,
        "type": "NEWS",
        "created_at": "2026-11-20T12:00:00Z",
        "payload": {"x": 1},
    }
    page = registry(lambda r: httpx.Response(200, json={"messages": [unknown], "has_more": False})).outbox(
        100
    )
    assert [(m.message_type, m.payload) for m in page.messages] == [("NEWS", None)]


ALERT = {
    "case_id": str(uuid.uuid4()),
    "role": "HOLDER",
    "asset_token": "d" * 64,
    "match_kind": "EXACT",
    "match_basis": "ASSET",
    "kinds_matched": ["ASSET"],
    "opened_seq": 4,
}


BODY = {"schema_version": 1, "checkpoint_seq": 3, "last_event_seq": 9000, "head_hash": "a" * 64,
        "signed_at": "2026-11-22T00:00:00Z"}  # fmt: skip
SIGNED = {"body": BODY, "signature": "A" * 86 + "==", "signing_key_id": "registry-local-1"}
HUGE = "<a 5000 digit integer literal>"
CASE = {"case_id": str(uuid.uuid4()), "asset_token": "d" * 64, "status": "CONFIRMED", "resolved_seq": 600}


def message(message_type: str, payload: object) -> dict[str, object]:
    return {
        "message_id": str(uuid.uuid4()),
        "position": 1,
        "type": message_type,
        "created_at": "2026-11-20T12:00:00Z",
        "payload": payload,
    }


QUARANTINE_CASES = [
    ("a valid alert", message("ALERT", ALERT), None),
    (
        "an asset code in capitals",
        message("ALERT", {**ALERT, "asset_token": "D" * 64}),
        "SCHEMA_MISMATCH",
    ),
    (
        "a short asset code",
        message("ALERT", {**ALERT, "asset_token": "d" * 63}),
        "SCHEMA_MISMATCH",
    ),
    ("a missing field", message("ALERT", {k: v for k, v in ALERT.items() if k != "role"}), "SCHEMA_MISMATCH"),
    (
        "a bad code and a missing field",
        message("ALERT", {**ALERT, "asset_token": "x", "role": None}),
        "SCHEMA_MISMATCH",
    ),
    ("no payload", message("CASE_UPDATE", None), "SCHEMA_MISMATCH"),
    ("a fraction", message("ALERT", {**ALERT, "opened_seq": 4.5}), "SCHEMA_MISMATCH"),
    ("an exponent", message("CHECKPOINT", {"body": {"checkpoint_seq": 1e3}}), "SCHEMA_MISMATCH"),
    ("over 64 KiB", message("CASE_UPDATE", {"case_id": "x", "notes": "n" * 66_000}), "OVERSIZED"),
    (
        "a kind listed twice",
        message("ALERT", {**ALERT, "kinds_matched": ["ASSET", "ASSET", "VIN"]}),
        "SCHEMA_MISMATCH",
    ),
    (
        "no opened_seq",
        message("ALERT", {k: v for k, v in ALERT.items() if k != "opened_seq"}),
        "SCHEMA_MISMATCH",
    ),
    (
        "no created_at",
        {k: v for k, v in message("ALERT", ALERT).items() if k != "created_at"},
        "SCHEMA_MISMATCH",
    ),
    (
        "a seq of 2 to the 63",
        message("CHECKPOINT", {**SIGNED, "body": {**BODY, "checkpoint_seq": 2**63}}),
        "VALUE_OUT_OF_RANGE",
    ),
    (
        "a 31 digit seq",
        message("CHECKPOINT", {**SIGNED, "body": {**BODY, "last_event_seq": 10**30}}),
        "VALUE_OUT_OF_RANGE",
    ),
    ("a 5000 digit seq", message("ALERT", {**ALERT, "opened_seq": HUGE}), "VALUE_OUT_OF_RANGE"),
    (
        "a too large seq and a missing field",
        message("ALERT", {**{k: v for k, v in ALERT.items() if k != "role"}, "opened_seq": 2**63}),
        "VALUE_OUT_OF_RANGE",  # SPEC_DECISIONS 100 first: a value the bank cannot store
    ),
    ("a position of 2 to the 63", {**message("CASE_UPDATE", CASE), "position": 2**63}, "VALUE_OUT_OF_RANGE"),
]


@pytest.mark.parametrize("case", QUARANTINE_CASES, ids=[c[0] for c in QUARANTINE_CASES])
def test_a_known_message_outside_the_contract_is_quarantined_with_its_reason_and_hash(
    case: tuple[str, dict[str, object], str | None],
) -> None:
    """SPEC_DECISIONS 85 rule 1: the whole schema and the storable range; the page is never refused."""
    _, sent, reason = case
    body = json.dumps({"messages": [sent], "has_more": False}).replace(f'"{HUGE}"', "9" * 5000).encode()
    (received,) = registry(lambda r: httpx.Response(200, content=body)).outbox(100).messages
    if reason is None:
        assert not isinstance(received.payload, QuarantinedPayload)
        return
    assert received.position == (None if "position of" in case[0] else 1)
    assert isinstance(received.payload, QuarantinedPayload)
    quarantined = received.payload
    assert (received.message_type, quarantined.reason.value) == (sent["type"], reason)
    assert len(quarantined.payload_sha256) == 64
    as_text = any(word in case[0] for word in ("fraction", "exponent"))  # a float is kept as its text
    if reason == "OVERSIZED" or (
        not as_text and not storable_json(sent["payload"])
    ):  # withheld (decision 100)
        assert quarantined.payload is None and quarantined.withheld
    elif not any(word in case[0] for word in ("fraction", "exponent", "5000")):
        assert quarantined.payload == sent["payload"]
        assert quarantined.payload_sha256 == hashlib.sha256(canonical_json(sent["payload"])).hexdigest()


def test_an_envelope_without_a_message_id_is_still_a_malformed_page() -> None:
    broken = {k: v for k, v in message("ALERT", ALERT).items() if k != "message_id"}
    with pytest.raises(RegistryProtocolError) as raised:
        registry(lambda r: httpx.Response(200, json={"messages": [broken], "has_more": False})).outbox(100)
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE
