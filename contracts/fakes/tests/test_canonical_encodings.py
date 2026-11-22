"""Identifiers and encodings are canonical or refused; a client hang-up is no failure (SPEC_DECISIONS 75)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import uuid
from collections.abc import Iterator, MutableMapping
from typing import Any

import oprf
import pytest
from fastapi import FastAPI

from onepledge_contract_fakes.key_holder.app import create_key_holder_app
from onepledge_contract_fakes.logging_setup import configure_logging
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
from onepledge_contract_fakes.validation import decode_canonical_base64, is_canonical_uuid

pytestmark = pytest.mark.contract

CANONICAL = "1f0e5f7c-7f3a-4b8e-9d55-2c1f0a9b3e77"
SPELLINGS = {
    "braces": "{" + CANONICAL + "}",
    "urn": "urn:uuid:" + CANONICAL,
    "upper_case": CANONICAL.upper(),
    "no_hyphens": CANONICAL.replace("-", ""),
}
B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def code(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def element(seed: bytes = b"p") -> str:
    return base64.b64encode(bytes(oprf.data.hash(seed))).decode()


def padding_bits_set(canonical: str) -> str:
    """The same 32 bytes with a non-zero padding bit in the last character before `=`."""
    last = B64_ALPHABET.index(canonical[-2])
    assert last % 4 == 0
    return canonical[:-2] + B64_ALPHABET[last + 1] + "="


@pytest.mark.unit
def test_only_the_canonical_uuid_spelling_is_canonical() -> None:
    assert is_canonical_uuid(CANONICAL)
    assert not any(is_canonical_uuid(spelling) for spelling in SPELLINGS.values())


@pytest.mark.unit
def test_base64_is_accepted_only_when_it_re_encodes_to_the_same_text() -> None:
    text = element()
    assert decode_canonical_base64(text) == base64.b64decode(text)
    assert base64.b64decode(padding_bits_set(text)) == base64.b64decode(text)
    assert decode_canonical_base64(padding_bits_set(text)) is None
    assert decode_canonical_base64(text.rstrip("=")) is None


@pytest.fixture
def key_holder() -> Iterator[tuple[AsgiTestClient, str]]:
    members, keys = new_members(1)
    with AsgiTestClient(create_key_holder_app(key_holder_config("kh1", members), FixedClock())) as client:
        yield client, keys[0]


@pytest.fixture
def registry() -> Iterator[tuple[AsgiTestClient, str]]:
    members, keys = new_members(1)
    app = create_registry_app(registry_config(members, new_api_key()), FixedClock())
    with AsgiTestClient(app) as client:
        yield client, keys[0]


def details(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [(d["field"], d["issue"]) for d in body["error"].get("details", [])]


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_a_request_id_in_another_spelling_is_pattern_mismatch(
    key_holder: tuple[AsgiTestClient, str], spelling: str
) -> None:
    client, key = key_holder
    body = {"request_id": SPELLINGS[spelling], "elements": [element()]}
    response = client.post("/v1/evaluate", json=body, headers={"X-API-Key": key})
    assert response.status_code == 422
    assert details(response.json()) == [("request_id", "PATTERN_MISMATCH")]


def pledge(item_ref: str) -> dict[str, Any]:
    item = {"item_ref": item_ref, "asset_token": code("a"), "field_tokens": {k: code(k) for k in FIELD_KINDS}}
    return {"schema_version": 1, "kind": "PLEDGE", "key_version": "1.1", "items": [item]}


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_an_item_ref_in_another_spelling_is_pattern_mismatch_and_never_rewritten(
    registry: tuple[AsgiTestClient, str], spelling: str
) -> None:
    client, key = registry
    headers = {"X-API-Key": key, "Idempotency-Key": code(spelling)}
    response = client.post("/v1/submissions", json=pledge(SPELLINGS[spelling]), headers=headers)
    assert response.status_code == 422
    assert details(response.json()) == [("items[0].item_ref", "PATTERN_MISMATCH")]


def test_a_path_id_in_another_spelling_is_404(registry: tuple[AsgiTestClient, str]) -> None:
    client, key = registry
    headers = {"X-API-Key": key, "Idempotency-Key": code("path")}
    created = client.post("/v1/submissions", json=pledge(str(uuid.uuid4())), headers=headers).json()
    submission_id = created["submission_id"]
    assert client.get(f"/v1/submissions/{submission_id}", headers={"X-API-Key": key}).status_code == 200
    for spelling in (submission_id.upper(), "{" + submission_id + "}", submission_id.replace("-", "")):
        assert client.get(f"/v1/submissions/{spelling}", headers={"X-API-Key": key}).status_code == 404


def test_a_point_with_non_zero_padding_bits_is_not_a_valid_point(
    key_holder: tuple[AsgiTestClient, str],
) -> None:
    client, key = key_holder
    body = {"request_id": CANONICAL, "elements": [element(), padding_bits_set(element(b"q"))]}
    response = client.post("/v1/evaluate", json=body, headers={"X-API-Key": key})
    assert response.status_code == 422
    assert details(response.json()) == [("elements[1]", "NOT_A_VALID_POINT")]


def test_an_x_request_id_in_another_spelling_is_replaced_not_rewritten(
    key_holder: tuple[AsgiTestClient, str],
) -> None:
    client, _ = key_holder
    assert client.get("/healthz", headers={"X-Request-ID": CANONICAL}).headers["x-request-id"] == CANONICAL
    for spelling in SPELLINGS.values():
        echoed = client.get("/healthz", headers={"X-Request-ID": spelling}).headers["x-request-id"]
        assert is_canonical_uuid(echoed) and echoed != CANONICAL


def hang_up(app: FastAPI) -> list[dict[str, Any]]:
    """Send the headers of a 500 byte POST, 20 bytes of body, then the client's disconnect."""
    messages: Iterator[dict[str, Any]] = iter(
        [
            {"type": "http.request", "body": b'{"schema_version": 1', "more_body": True},
            {"type": "http.disconnect"},
        ]
    )
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return next(messages)

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(dict(message))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/submissions",
        "raw_path": b"/v1/submissions",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"content-length", b"500")],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 2),
    }
    asyncio.run(app(scope, receive, send))
    return sent


def test_a_client_that_hangs_up_mid_body_is_one_info_record_and_no_failure() -> None:
    stream = io.StringIO()
    configure_logging("registry", "INFO", stream)
    members, _ = new_members(1)
    sent = hang_up(create_registry_app(registry_config(members, new_api_key()), FixedClock()))
    records = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    assert [(r["level"], r["event"]) for r in records] == [("info", "client_disconnected")]
    assert not [m for m in sent if m["type"] == "http.response.start"]
