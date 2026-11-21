"""Live HTTP responses of the registry double validate against contracts/registry.yaml."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from onepledge_contract_fakes.contracts_check.schemas import ContractSchemas
from onepledge_contract_fakes.registry.app import create_registry_app
from onepledge_contract_fakes.registry.model import FIELD_KINDS
from onepledge_contract_fakes.testing import (
    AsgiTestClient,
    FixedClock,
    new_api_key,
    new_members,
    registry_config,
)

pytestmark = pytest.mark.contract

CONTROL_KEY = "t" * 40
CONTROL = {"X-Test-Control-Key": CONTROL_KEY}


@dataclass
class Http:
    client: AsgiTestClient
    keys: list[str]
    auditor: str
    schemas: ContractSchemas

    def member(self, index: int, **extra: str) -> dict[str, str]:
        return {"X-API-Key": self.keys[index], **extra}

    def check(self, path: str, method: str, response: httpx.Response) -> dict[str, Any]:
        payload: dict[str, Any] | None = response.json() if response.content else None
        assert self.schemas.response_errors(path, method, response.status_code, payload) == []
        return payload or {}


def code(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def submission(*names: str, kind: str = "PLEDGE") -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for name in names:
        entry: dict[str, Any] = {"item_ref": str(uuid.uuid4()), "asset_token": code("asset" + name)}
        if kind != "RELEASE":
            entry["field_tokens"] = {k: code(k + name) for k in FIELD_KINDS}
        items.append(entry)
    return {"schema_version": 1, "kind": kind, "key_version": "1.1", "items": items}


@pytest.fixture
def http(registry_schemas: ContractSchemas) -> Iterator[Http]:
    members, keys = new_members(2)
    auditor = new_api_key()
    app = create_registry_app(registry_config(members, auditor, test_control_key=CONTROL_KEY), FixedClock())
    with AsgiTestClient(app) as client:
        yield Http(client, list(keys), auditor, registry_schemas)


def post_submission(http: Http, member: int, body: dict[str, Any], key: str) -> httpx.Response:
    return http.client.post(
        "/v1/submissions", json=body, headers=http.member(member, **{"Idempotency-Key": key})
    )


def test_every_contract_endpoint_validates(http: Http) -> None:
    created = post_submission(http, 0, submission("a", "b"), code("k1"))
    result = http.check("/v1/submissions", "post", created)
    assert created.status_code == 201
    conflict = http.check("/v1/submissions", "post", post_submission(http, 1, submission("a"), code("k2")))
    case_id = conflict["results"][0]["case_id"]
    http.check(
        "/v1/submissions/{submission_id}",
        "get",
        http.client.get(f"/v1/submissions/{result['submission_id']}", headers=http.member(0)),
    )
    http.check(
        "/v1/certificates/{certificate_id}",
        "get",
        http.client.get(f"/v1/certificates/{result['certificate_id']}", headers=http.member(0)),
    )
    outbox = http.check("/v1/outbox", "get", http.client.get("/v1/outbox?limit=200", headers=http.member(1)))
    assert {m["type"] for m in outbox["messages"]} == {"CHECKPOINT", "ALERT"}
    acked = http.client.post(f"/v1/outbox/{outbox['messages'][0]['message_id']}/ack", headers=http.member(1))
    assert acked.status_code == 204
    http.check("/v1/cases", "get", http.client.get("/v1/cases?status=OPEN", headers=http.member(1)))
    position = http.client.post(
        f"/v1/cases/{case_id}/resolution", json={"position": "DATA_ERROR"}, headers=http.member(1)
    )
    http.check("/v1/cases/{case_id}/resolution", "post", position)
    http.check("/v1/ledger/events", "get", http.client.get("/v1/ledger/events", headers=http.member(0)))
    full = http.client.get("/v1/ledger/events?view=full", headers={"X-API-Key": http.auditor})
    assert all("payload" in e for e in http.check("/v1/ledger/events", "get", full)["events"])
    http.check(
        "/v1/checkpoints/latest", "get", http.client.get("/v1/checkpoints/latest", headers=http.member(0))
    )
    health = http.check("/healthz", "get", http.client.get("/healthz"))
    assert health["test_double"] is True
    released = post_submission(http, 0, submission("b", kind="RELEASE"), code("k3"))
    assert http.check("/v1/submissions", "post", released)["results"][0]["result"] == "RELEASED"


def test_replay_returns_the_same_bytes_with_the_replay_header(http: Http) -> None:
    body = submission("a")
    first = post_submission(http, 0, body, code("k"))
    replay = post_submission(http, 0, body, code("k"))
    assert replay.status_code == 200 and replay.headers["idempotent-replay"] == "true"
    assert replay.content == first.content
    http.check("/v1/submissions", "post", replay)
    reused = post_submission(http, 0, submission("other"), code("k"))
    assert reused.status_code == 409 and reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    http.check("/v1/submissions", "post", reused)


def test_errors_validate_and_never_echo_values(http: Http) -> None:
    canary = "ZQXJ7CANARY9"
    bad_version = {**submission("a"), "key_version": "9.9"}
    cases: list[tuple[str, str, httpx.Response, int, str]] = [
        (
            "/v1/submissions",
            "post",
            http.client.post("/v1/submissions", json=submission("a")),
            401,
            "UNAUTHENTICATED",
        ),
        (
            "/v1/submissions",
            "post",
            post_submission(http, 0, submission("a"), canary),
            422,
            "VALIDATION_ERROR",
        ),
        (
            "/v1/submissions",
            "post",
            post_submission(http, 0, {**submission("a"), "kind": canary}, code("x")),
            422,
            "VALIDATION_ERROR",
        ),
        (
            "/v1/submissions",
            "post",
            post_submission(http, 0, bad_version, code("y")),
            409,
            "KEY_VERSION_MISMATCH",
        ),
        (
            "/v1/submissions/{submission_id}",
            "get",
            http.client.get(f"/v1/submissions/{canary}", headers=http.member(0)),
            404,
            "SUBMISSION_NOT_FOUND",
        ),
        (
            "/v1/certificates/{certificate_id}",
            "get",
            http.client.get(f"/v1/certificates/{uuid.uuid4()}", headers=http.member(0)),
            404,
            "CERTIFICATE_NOT_FOUND",
        ),
        (
            "/v1/ledger/events",
            "get",
            http.client.get("/v1/ledger/events?view=full", headers=http.member(0)),
            403,
            "FORBIDDEN",
        ),
        (
            "/v1/outbox/{message_id}/ack",
            "post",
            http.client.post(f"/v1/outbox/{uuid.uuid4()}/ack", headers=http.member(0)),
            404,
            "OUTBOX_MESSAGE_NOT_FOUND",
        ),
        (
            "/v1/cases/{case_id}/resolution",
            "post",
            http.client.post(
                f"/v1/cases/{uuid.uuid4()}/resolution", json={"position": canary}, headers=http.member(0)
            ),
            422,
            "VALIDATION_ERROR",
        ),
    ]
    for path, method, response, status, error_code in cases:
        assert (response.status_code, response.json()["error"]["code"]) == (status, error_code), path
        assert canary not in response.text
        http.check(path, method, response)


def test_a_lost_response_is_replayed_without_a_second_lock(http: Http) -> None:
    armed = http.client.post(
        "/_test/fail-next", json={"count": 1, "status": 503, "when": "AFTER_COMMIT"}, headers=CONTROL
    )
    assert armed.status_code == 200
    http.client.get("/v1/outbox", headers=http.member(0))
    body = submission("lost")
    lost = post_submission(http, 0, body, code("lost"))
    assert lost.status_code == 503
    retry = post_submission(http, 0, body, code("lost"))
    assert retry.status_code == 200 and retry.headers["idempotent-replay"] == "true"
    stats = http.client.get("/_test/stats", headers=CONTROL).json()
    assert stats["total"]["submissions"] == 1 and stats["total"]["active_locks"] == 1
    received = list(http.client.get("/_test/received", headers=CONTROL).json()["requests"])
    bodies = [r["body"] for r in received if r["path"] == "/v1/submissions"]
    assert len(bodies) == 2 and len(set(bodies)) == 1


def test_before_processing_failure_and_the_other_controls(http: Http) -> None:
    http.client.post(
        "/_test/fail-next", json={"count": 2, "status": 500, "when": "BEFORE_PROCESSING"}, headers=CONTROL
    )
    assert http.client.get("/v1/outbox", headers=http.member(0)).status_code == 500
    assert post_submission(http, 0, submission("a"), code("k")).status_code == 500
    assert post_submission(http, 0, submission("a"), code("k")).status_code == 201
    dump = http.client.get("/_test/dump", headers=CONTROL).json()
    assert len(dump["locks"]) == 1
    tampered = http.client.post("/_test/tamper", json={"mode": "EDIT_IN_PLACE", "seq": 1}, headers=CONTROL)
    assert tampered.status_code == 200
    assert http.client.post("/_test/tamper", json={"mode": "NOPE"}, headers=CONTROL).status_code == 422
    assert http.client.post("/_test/checkpoint", headers=CONTROL).status_code == 200
    stats = http.client.get("/_test/stats", headers=CONTROL).json()
    assert set(stats["total"]) == {
        "submissions",
        "items",
        "active_locks",
        "open_cases",
        "events",
        "checkpoints",
    }
    assert all(
        set(entry) == {"submissions", "items", "active_locks", "open_cases"}
        for entry in stats["by_member"].values()
    )
    limited = http.client.post(
        "/_test/fail-next", json={"count": 1, "status": 429, "when": "BEFORE_PROCESSING"}, headers=CONTROL
    )
    assert limited.status_code == 200
    rate = http.client.get("/v1/cases", headers=http.member(0))
    assert rate.status_code == 429 and rate.headers["retry-after"] == "1"
    assert http.client.get("/_test/dump").status_code == 401
    assert http.client.post("/_test/fail-next", json={"count": 0}, headers=CONTROL).status_code == 422


def test_query_bounds_and_auditor_reads(http: Http) -> None:
    assert http.client.get("/v1/outbox?limit=0", headers=http.member(0)).status_code == 422
    assert http.client.get("/v1/ledger/events?limit=1001", headers=http.member(0)).status_code == 422
    assert http.client.get("/v1/checkpoints/latest", headers={"X-API-Key": http.auditor}).status_code == 200
    assert http.client.get("/v1/checkpoints/latest").status_code == 401
    assert http.client.get("/v1/outbox", headers={"X-API-Key": http.auditor}).status_code == 401
    page = http.client.get("/v1/ledger/events?after_seq=1&limit=1", headers=http.member(0)).json()
    assert [e["seq"] for e in page["events"]] == [2] and page["head_seq"] >= 3


def test_controls_are_absent_when_disabled() -> None:
    members, _ = new_members(1)
    with AsgiTestClient(create_registry_app(registry_config(members, new_api_key()))) as client:
        assert client.get("/_test/stats", headers=CONTROL).status_code == 404


def test_submission_body_shapes_in_received_contain_only_contract_keys(http: Http) -> None:
    post_submission(http, 0, submission("a"), code("k"))
    received = http.client.get("/_test/received", headers=CONTROL).json()["requests"]
    keys = set(json.loads(received[0]["body"]))
    assert keys == {"schema_version", "kind", "key_version", "items"}
