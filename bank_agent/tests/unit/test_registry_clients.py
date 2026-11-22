"""The key holder and registry clients over a mocked network boundary (SPEC 13.3, 13.4, 16.6).

Answers come from scripted handlers. Every error is named with its dependency, HTTP status
and reason; a malformed answer, a float in a signed body or a replay header that disagrees
with the status is a protocol error; unknown fields are ignored; every send is counted.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable

import httpx
import pytest

from bank_testkit.clocks import CountingIds
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
from onepledge_bank_agent.domain.enums import BankPosition, CaseStatus, DependencyRefusal, TapeKind, TokenKind
from onepledge_bank_agent.domain.errors import PipelineStageError, PositionAlreadyRecordedError
from onepledge_bank_agent.domain.registry_records import RegistryItem, RegistrySubmission
from onepledge_bank_agent.infrastructure.http.base import RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.key_holder_client import KeyHolderHttpClient
from onepledge_bank_agent.infrastructure.http.registry_client import RegistryHttpClient

pytestmark = pytest.mark.unit

type Handler = Callable[[httpx.Request], httpx.Response]
POINT = base64.b64encode(bytes(range(1, 33))).decode()
ITEM_REF, TOKEN = uuid.UUID(int=7, version=4), "a" * 64


class Quiet:
    def info(self, event: str, **fields: object) -> None:
        """Nothing."""

    def warning(self, event: str, **fields: object) -> None:
        """Nothing."""

    def failure(self, event: str, error: BaseException, **fields: object) -> None:
        """Nothing."""


def caller(handler: Handler, dependency: str, seen: list[httpx.Request]) -> RetryingHttpCaller:
    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(base_url="http://service.test", transport=httpx.MockTransport(record))
    return RetryingHttpCaller(client, "k" * 40, dependency, Quiet(), lambda _: None)


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


MALFORMED = DependencyRefusal.MALFORMED_RESPONSE
ZERO_POINT = base64.b64encode(bytes(32)).decode()
KEY_HOLDER_FAILURES: list[tuple[Handler, type[PipelineStageError], int, DependencyRefusal | None]] = [
    (lambda r: httpx.Response(503), KeyHolderUnavailableError, 503, None),
    (lambda r: httpx.Response(429), KeyHolderUnavailableError, 429, None),
    (lambda r: httpx.Response(401), KeyHolderRejectedError, 401, DependencyRefusal.UNAUTHENTICATED),
    (lambda r: httpx.Response(422), KeyHolderRejectedError, 422, DependencyRefusal.REQUEST_REFUSED),
    (lambda r: evaluated(r, elements=[POINT]), KeyHolderProtocolError, 200, MALFORMED),
    (lambda r: evaluated(r, request_id=str(uuid.uuid4())), KeyHolderProtocolError, 200, MALFORMED),
    (lambda r: evaluated(r, elements=[ZERO_POINT] * 2), KeyHolderProtocolError, 200, MALFORMED),
    (lambda r: evaluated(r, key_version=1.0), KeyHolderProtocolError, 200, MALFORMED),
    (lambda r: httpx.Response(200, content=b"not json"), KeyHolderProtocolError, 200, MALFORMED),
]


@pytest.mark.parametrize(("handler", "error", "status", "reason"), KEY_HOLDER_FAILURES)
def test_key_holder_failures_are_named_with_dependency_status_and_reason(
    handler: Handler, error: type[PipelineStageError], status: int, reason: DependencyRefusal | None
) -> None:
    with pytest.raises(error) as raised:
        holder(handler).evaluate([POINT, POINT])
    assert raised.value.audit_detail() == {
        "dependency": "key_holder_2",
        "http_status": status,
        **({"reason": reason.value} if reason is not None else {}),
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


def registry(handler: Handler, seen: list[httpx.Request] | None = None) -> RegistryHttpClient:
    return RegistryHttpClient(caller(handler, "registry", seen if seen is not None else []))


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
    (lambda r: decided(r, as_of_seq=9.0), RegistryProtocolError),
    (lambda r: decided(r, results="LOCKED"), RegistryProtocolError),
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
