"""Repeated headers (SPEC_DECISIONS 112.8): a repeated credential is 401, another listed one 422 DUPLICATE."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Iterator

import oprf
import pytest

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

pytestmark = pytest.mark.contract

CONTROL_KEY = "c" * 40
JSON = ("Content-Type", "application/json")


def code(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def pledge() -> bytes:
    item = {
        "item_ref": str(uuid.uuid4()),
        "asset_token": code("a"),
        "field_tokens": {k: code(k) for k in FIELD_KINDS},
    }
    return json.dumps({"schema_version": 1, "kind": "PLEDGE", "key_version": "1.1", "items": [item]}).encode()


@pytest.fixture
def registry() -> Iterator[tuple[AsgiTestClient, str]]:
    members, keys = new_members(1)
    config = registry_config(members, new_api_key(), test_control_key=CONTROL_KEY)
    with AsgiTestClient(create_registry_app(config, FixedClock())) as client:
        yield client, keys[0]


@pytest.fixture
def key_holder() -> Iterator[tuple[AsgiTestClient, str]]:
    members, keys = new_members(1)
    with AsgiTestClient(create_key_holder_app(key_holder_config("kh1", members), FixedClock())) as client:
        yield client, keys[0]


def problem(response_body: bytes) -> tuple[str, list[tuple[str, str]]]:
    error = json.loads(response_body)["error"]
    return error["code"], [(d["field"], d["issue"]) for d in error.get("details", [])]


def test_a_repeated_api_key_is_unauthenticated_even_when_both_are_valid(
    registry: tuple[AsgiTestClient, str],
) -> None:
    client, key = registry
    headers = [("X-API-Key", key), ("X-API-Key", key), JSON, ("Idempotency-Key", code("k"))]
    response = client.send("POST", "/v1/submissions", pledge(), headers)
    assert (response.status_code, problem(response.content)) == (401, ("UNAUTHENTICATED", []))


def test_a_repeated_idempotency_key_is_duplicate_among_the_field_problems(
    registry: tuple[AsgiTestClient, str],
) -> None:
    client, key = registry
    headers = [("X-API-Key", key), JSON, ("Idempotency-Key", code("a")), ("Idempotency-Key", code("b"))]
    response = client.send("POST", "/v1/submissions", pledge(), headers)
    assert (response.status_code, problem(response.content)) == (
        422,
        ("VALIDATION_ERROR", [("Idempotency-Key", "DUPLICATE")]),
    )


def test_a_repeated_idempotency_key_of_an_unauthenticated_caller_is_401_first(
    registry: tuple[AsgiTestClient, str],
) -> None:
    client, _ = registry
    headers = [JSON, ("Idempotency-Key", code("a")), ("Idempotency-Key", code("b"))]
    assert client.send("POST", "/v1/submissions", pledge(), headers).status_code == 401


def test_a_repeated_content_type_is_duplicate(key_holder: tuple[AsgiTestClient, str]) -> None:
    client, key = key_holder
    body = json.dumps({"request_id": str(uuid.uuid4()), "elements": [element()]}).encode()
    headers = [("X-API-Key", key), JSON, JSON]
    response = client.send("POST", "/v1/evaluate", body, headers)
    assert (response.status_code, problem(response.content)) == (
        422,
        ("VALIDATION_ERROR", [("Content-Type", "DUPLICATE")]),
    )


def test_a_repeated_content_length_is_duplicate(key_holder: tuple[AsgiTestClient, str]) -> None:
    client, key = key_holder
    body = json.dumps({"request_id": str(uuid.uuid4()), "elements": [element()]}).encode()
    length = str(len(body))
    headers = [("X-API-Key", key), JSON, ("Content-Length", length), ("Content-Length", length)]
    response = client.send("POST", "/v1/evaluate", body, headers)
    assert (response.status_code, problem(response.content)) == (
        422,
        ("VALIDATION_ERROR", [("Content-Length", "DUPLICATE")]),
    )


def test_a_repeated_request_id_is_duplicate(key_holder: tuple[AsgiTestClient, str]) -> None:
    client, _ = key_holder
    request_id = str(uuid.uuid4())
    response = client.send(
        "GET", "/healthz", None, [("X-Request-ID", request_id), ("X-Request-ID", request_id)]
    )
    assert (response.status_code, problem(response.content)) == (
        422,
        ("VALIDATION_ERROR", [("X-Request-ID", "DUPLICATE")]),
    )


def test_a_repeated_test_control_key_is_unauthenticated(registry: tuple[AsgiTestClient, str]) -> None:
    client, _ = registry
    headers = [("X-Test-Control-Key", CONTROL_KEY), ("X-Test-Control-Key", CONTROL_KEY)]
    assert client.send("GET", "/_test/stats", None, headers).status_code == 401


def element() -> str:
    return base64.b64encode(bytes(oprf.data.hash(b"p"))).decode()
