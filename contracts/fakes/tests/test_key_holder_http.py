"""Live HTTP responses of the key holder double validate against contracts/key_holder.yaml."""

from __future__ import annotations

import base64
from collections.abc import Iterator

import httpx
import oprf
import pytest

from onepledge_contract_fakes.contracts_check.schemas import ContractSchemas
from onepledge_contract_fakes.key_holder.app import create_key_holder_app
from onepledge_contract_fakes.testing import AsgiTestClient, FixedClock, key_holder_config, new_members

pytestmark = pytest.mark.contract

CONTROL_KEY = "k" * 40
REQUEST_ID = "1f0e5f7c-7f3a-4b8e-9d55-2c1f0a9b3e77"


@pytest.fixture
def setup() -> Iterator[tuple[AsgiTestClient, str]]:
    members, keys = new_members(1)
    config = key_holder_config("kh2", members, daily_element_cap=10, test_control_key=CONTROL_KEY)
    with AsgiTestClient(create_key_holder_app(config, FixedClock())) as client:
        yield client, keys[0]


def element(seed: bytes = b"p") -> str:
    return base64.b64encode(bytes(oprf.data.hash(seed))).decode()


def body(elements: list[str], **extra: object) -> dict[str, object]:
    return {"request_id": REQUEST_ID, "elements": elements, **extra}


def check(schemas: ContractSchemas, path: str, method: str, response: httpx.Response) -> None:
    payload = response.json() if response.content else None
    assert schemas.response_errors(path, method, response.status_code, payload) == []


def test_evaluate_and_health_validate(
    setup: tuple[AsgiTestClient, str], key_holder_schemas: ContractSchemas
) -> None:
    client, key = setup
    ok = client.post("/v1/evaluate", json=body([element()], key_version=None), headers={"X-API-Key": key})
    assert ok.status_code == 200 and ok.json()["holder_id"] == "kh2"
    check(key_holder_schemas, "/v1/evaluate", "post", ok)
    health = client.get("/healthz")
    check(key_holder_schemas, "/healthz", "get", health)
    assert health.json()["test_double"] is True


def test_error_responses_validate_and_echo_nothing(
    setup: tuple[AsgiTestClient, str], key_holder_schemas: ContractSchemas
) -> None:
    client, key = setup
    canary = "ZQXJ7CANARY9"
    cases = [
        (client.post("/v1/evaluate", json=body([element()])), 401, "UNAUTHENTICATED"),
        (
            client.post("/v1/evaluate", json=body([canary]), headers={"X-API-Key": key}),
            422,
            "VALIDATION_ERROR",
        ),
        (
            client.post("/v1/evaluate", json={"request_id": canary}, headers={"X-API-Key": key}),
            422,
            "VALIDATION_ERROR",
        ),
        (
            client.post(
                "/v1/evaluate", json=body([base64.b64encode(bytes(32)).decode()]), headers={"X-API-Key": key}
            ),
            422,
            "INVALID_ELEMENT",
        ),
        (
            client.post("/v1/evaluate", json=body([element()], key_version=9), headers={"X-API-Key": key}),
            409,
            "KEY_VERSION_UNAVAILABLE",
        ),
        (client.post_raw("/v1/evaluate", b"{", headers={"X-API-Key": key}), 422, "VALIDATION_ERROR"),
    ]
    for response, status, code in cases:
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert canary not in response.text
        assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
        check(key_holder_schemas, "/v1/evaluate", "post", response)


def test_rate_limit_has_retry_after(
    setup: tuple[AsgiTestClient, str], key_holder_schemas: ContractSchemas
) -> None:
    client, key = setup
    many = [element(bytes([i])) for i in range(6)]
    assert client.post("/v1/evaluate", json=body(many), headers={"X-API-Key": key}).status_code == 200
    limited = client.post("/v1/evaluate", json=body(many), headers={"X-API-Key": key})
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1
    check(key_holder_schemas, "/v1/evaluate", "post", limited)


def test_request_id_is_echoed_when_valid_and_replaced_otherwise(setup: tuple[AsgiTestClient, str]) -> None:
    client, _ = setup
    assert client.get("/healthz", headers={"X-Request-ID": REQUEST_ID}).headers["x-request-id"] == REQUEST_ID
    replaced = client.get("/healthz", headers={"X-Request-ID": "not-a-uuid"}).headers["x-request-id"]
    assert replaced != "not-a-uuid" and len(replaced) == 36


def test_unknown_route_and_method_use_the_error_envelope(setup: tuple[AsgiTestClient, str]) -> None:
    client, _ = setup
    assert client.get("/nowhere").json()["error"]["code"] == "NOT_FOUND"
    assert client.get("/v1/evaluate").json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_body_over_the_limit_is_refused(setup: tuple[AsgiTestClient, str]) -> None:
    client, key = setup
    response = client.post_raw("/v1/evaluate", b"x" * 1_100_000, headers={"X-API-Key": key})
    assert response.status_code == 413 and response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_test_controls_need_their_key_and_record_bodies(setup: tuple[AsgiTestClient, str]) -> None:
    client, key = setup
    assert client.get("/_test/stats").status_code == 401
    assert client.get("/_test/stats", headers={"X-Test-Control-Key": "wrong"}).status_code == 401
    control = {"X-Test-Control-Key": CONTROL_KEY}
    armed = client.post(
        "/_test/fail-next", json={"count": 1, "status": 503, "when": "BEFORE_PROCESSING"}, headers=control
    )
    assert armed.status_code == 200
    failed = client.post("/v1/evaluate", json=body([element()]), headers={"X-API-Key": key})
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert client.post("/v1/evaluate", json=body([element()]), headers={"X-API-Key": key}).status_code == 200
    received = client.get("/_test/received", headers=control).json()["requests"]
    assert len(received) == 2 and received[0]["path"] == "/v1/evaluate" and received[0]["member_id"]
    stats = client.get("/_test/stats", headers=control).json()
    assert stats["total"] == {"requests": 1, "elements": 1}
    refused = client.post(
        "/_test/fail-next", json={"count": 1, "status": 503, "when": "AFTER_COMMIT"}, headers=control
    )
    assert refused.status_code == 422


def test_test_controls_are_absent_when_disabled() -> None:
    members, _ = new_members(1)
    with AsgiTestClient(create_key_holder_app(key_holder_config("kh1", members), FixedClock())) as client:
        assert client.get("/_test/stats", headers={"X-Test-Control-Key": CONTROL_KEY}).status_code == 404
