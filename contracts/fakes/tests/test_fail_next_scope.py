"""`/_test/fail-next` with and without the `path` route filter (SPEC 7.3, SPEC_DECISIONS 6)."""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import oprf
import pytest
import yaml

from onepledge_contract_fakes.controls import ControlState, FailureSpec, FailureWhen, parse_failure_spec
from onepledge_contract_fakes.errors import ValidationFailedError
from onepledge_contract_fakes.key_holder.app import create_key_holder_app
from onepledge_contract_fakes.registry.app import create_registry_app
from onepledge_contract_fakes.registry.model import FIELD_KINDS
from onepledge_contract_fakes.testing import (
    AsgiTestClient,
    FixedClock,
    key_holder_config,
    new_api_key,
    new_members,
    registry_config,
)
from onepledge_contract_fakes.web import contract_routes

CONTROL_KEY = "c" * 40
CONTROL = {"X-Test-Control-Key": CONTROL_KEY}
REGISTRY_ROUTES = frozenset({"/v1/submissions", "/v1/outbox", "/v1/certificates/{certificate_id}"})


def code(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def submission(name: str) -> dict[str, Any]:
    item = {
        "item_ref": str(uuid.uuid4()),
        "asset_token": code("asset" + name),
        "field_tokens": {kind: code(kind + name) for kind in FIELD_KINDS},
    }
    return {"schema_version": 1, "kind": "PLEDGE", "key_version": "1.1", "items": [item]}


def yaml_paths(contracts_dir: Path, name: str) -> set[str]:
    document = yaml.safe_load((contracts_dir / name).read_text(encoding="utf-8"))
    return {path for path in document["paths"] if path != "/healthz"}


@pytest.fixture
def registry() -> Iterator[tuple[AsgiTestClient, str]]:
    members, keys = new_members(1)
    config = registry_config(members, new_api_key(), test_control_key=CONTROL_KEY)
    with AsgiTestClient(create_registry_app(config, FixedClock())) as client:
        yield client, keys[0]


@pytest.fixture
def key_holder() -> Iterator[tuple[AsgiTestClient, str]]:
    members, keys = new_members(1)
    config = key_holder_config("kh1", members, test_control_key=CONTROL_KEY)
    with AsgiTestClient(create_key_holder_app(config, FixedClock())) as client:
        yield client, keys[0]


def submit(client: AsgiTestClient, key: str, name: str) -> int:
    headers = {"X-API-Key": key, "Idempotency-Key": code("idem" + name)}
    return client.post("/v1/submissions", json=submission(name), headers=headers).status_code


def arm(client: AsgiTestClient, **document: object) -> tuple[int, dict[str, Any]]:
    response = client.post("/_test/fail-next", json=document, headers=CONTROL)
    body: dict[str, Any] = response.json()
    return response.status_code, body


@pytest.mark.unit
def test_a_route_filtered_failure_is_consumed_only_by_that_route() -> None:
    controls = ControlState()
    spec = parse_failure_spec(
        {"count": 2, "status": 503, "when": "BEFORE_PROCESSING", "path": "/v1/outbox"},
        REGISTRY_ROUTES,
        allow_after_commit=True,
    )
    assert spec == FailureSpec(2, 503, FailureWhen.BEFORE_PROCESSING, "/v1/outbox")
    controls.arm(spec)
    assert controls.take(FailureWhen.BEFORE_PROCESSING, "/v1/submissions") is None
    assert controls.take(FailureWhen.BEFORE_PROCESSING, "/v1/outbox") == 503
    assert controls.take(FailureWhen.BEFORE_PROCESSING, "/v1/certificates/{certificate_id}") is None
    assert controls.take(FailureWhen.BEFORE_PROCESSING, "/v1/outbox") == 503
    assert controls.take(FailureWhen.BEFORE_PROCESSING, "/v1/outbox") is None


@pytest.mark.unit
def test_without_a_path_every_contract_call_consumes_the_failure() -> None:
    controls = ControlState()
    controls.arm(
        parse_failure_spec({"count": 2, "status": 500, "when": "BEFORE_PROCESSING"}, REGISTRY_ROUTES, True)
    )
    assert controls.take(FailureWhen.BEFORE_PROCESSING, "/v1/outbox") == 500
    assert controls.take(FailureWhen.BEFORE_PROCESSING, "/v1/submissions") == 500
    assert controls.take(FailureWhen.BEFORE_PROCESSING, "/v1/submissions") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("document", "field", "issue"),
    [
        ({"path": "/v1/nowhere"}, "path", "INVALID_VALUE"),
        ({"path": "/healthz"}, "path", "INVALID_VALUE"),
        ({"path": "/_test/stats"}, "path", "INVALID_VALUE"),
        ({"path": "/v1/certificates/3f2a"}, "path", "INVALID_VALUE"),
        ({"path": 7}, "path", "INVALID_TYPE"),
        ({"path": "/v1/outbox", "when": "AFTER_COMMIT"}, "path", "INVALID_VALUE"),
        ({"ZQXJ7CANARY9": True}, "body", "UNKNOWN_FIELD"),
        ({"count": 1.0}, "count", "INVALID_TYPE"),
        ({"count": True}, "count", "INVALID_TYPE"),
        ({"count": 0}, "count", "OUT_OF_RANGE"),
        ({"status": 404}, "status", "INVALID_VALUE"),
        ({"status": "503"}, "status", "INVALID_TYPE"),
        ({"when": "LATER"}, "when", "INVALID_VALUE"),
    ],
)
def test_malformed_failure_requests_are_refused(document: dict[str, object], field: str, issue: str) -> None:
    full = {"count": 1, "status": 503, "when": "BEFORE_PROCESSING", **document}
    with pytest.raises(ValidationFailedError) as raised:
        parse_failure_spec(full, REGISTRY_ROUTES, allow_after_commit=True)
    assert [(d.field, d.issue) for d in raised.value.details] == [(field, issue)]


@pytest.mark.contract
def test_the_accepted_paths_are_the_contract_paths(contracts_dir: Path) -> None:
    members, _ = new_members(1)
    registry_app = create_registry_app(registry_config(members, new_api_key(), test_control_key=CONTROL_KEY))
    key_holder_app = create_key_holder_app(key_holder_config("kh1", members, test_control_key=CONTROL_KEY))
    assert contract_routes(registry_app) == yaml_paths(contracts_dir, "registry.yaml")
    assert contract_routes(key_holder_app) == yaml_paths(contracts_dir, "key_holder.yaml")


@pytest.mark.contract
def test_a_submission_failure_is_not_consumed_by_outbox_polls(registry: tuple[AsgiTestClient, str]) -> None:
    client, key = registry
    status, armed = arm(client, count=1, status=503, when="BEFORE_PROCESSING", path="/v1/submissions")
    assert (status, armed["path"]) == (200, "/v1/submissions")
    for _ in range(3):
        assert client.get("/v1/outbox", headers={"X-API-Key": key}).status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.get("/_test/stats", headers=CONTROL).status_code == 200
    assert submit(client, key, "first") == 503
    assert submit(client, key, "first") == 201


@pytest.mark.contract
def test_a_certificate_fetch_can_be_failed_alone(registry: tuple[AsgiTestClient, str]) -> None:
    client, key = registry
    headers = {"X-API-Key": key, "Idempotency-Key": code("cert")}
    certificate_id = client.post("/v1/submissions", json=submission("cert"), headers=headers).json()[
        "certificate_id"
    ]
    arm(client, count=1, status=502, when="BEFORE_PROCESSING", path="/v1/certificates/{certificate_id}")
    assert submit(client, key, "second") == 201
    assert client.get("/v1/outbox", headers={"X-API-Key": key}).status_code == 200
    url = f"/v1/certificates/{certificate_id}"
    assert client.get(url, headers={"X-API-Key": key}).status_code == 502
    assert client.get(url, headers={"X-API-Key": key}).status_code == 200


@pytest.mark.contract
def test_health_and_test_controls_never_consume_an_unfiltered_failure(
    registry: tuple[AsgiTestClient, str],
) -> None:
    client, key = registry
    arm(client, count=1, status=503, when="BEFORE_PROCESSING")
    assert client.get("/healthz").status_code == 200
    assert client.get("/_test/received", headers=CONTROL).status_code == 200
    assert client.get("/v1/outbox", headers={"X-API-Key": key}).status_code == 503
    assert client.get("/v1/outbox", headers={"X-API-Key": key}).status_code == 200


@pytest.mark.contract
def test_after_commit_may_name_the_submissions_route_only(registry: tuple[AsgiTestClient, str]) -> None:
    client, key = registry
    status, body = arm(client, count=1, status=503, when="AFTER_COMMIT", path="/v1/outbox")
    assert (status, body["error"]["details"]) == (
        422,
        [{"field": "path", "issue": "INVALID_VALUE"}],
    )
    assert arm(client, count=1, status=503, when="AFTER_COMMIT", path="/v1/submissions")[0] == 200
    assert client.get("/v1/outbox", headers={"X-API-Key": key}).status_code == 200
    body, headers = submission("lost"), {"X-API-Key": key, "Idempotency-Key": code("lost")}
    assert client.post("/v1/submissions", json=body, headers=headers).status_code == 503
    replay = client.post("/v1/submissions", json=body, headers=headers)
    assert (replay.status_code, replay.headers["idempotent-replay"]) == (200, "true")


@pytest.mark.contract
def test_the_key_holder_accepts_only_its_own_route(key_holder: tuple[AsgiTestClient, str]) -> None:
    client, key = key_holder
    status, body = arm(client, count=1, status=503, when="BEFORE_PROCESSING", path="/v1/submissions")
    assert (status, body["error"]["details"]) == (422, [{"field": "path", "issue": "INVALID_VALUE"}])
    assert arm(client, count=1, status=503, when="BEFORE_PROCESSING", path="/v1/evaluate")[0] == 200
    element = base64.b64encode(bytes(oprf.data.hash(b"p"))).decode()
    request = {"request_id": str(uuid.uuid4()), "elements": [element]}
    assert client.post("/v1/evaluate", json=request, headers={"X-API-Key": key}).status_code == 503
    assert client.post("/v1/evaluate", json=request, headers={"X-API-Key": key}).status_code == 200
