"""Strict input validation of both doubles over HTTP (SPEC 4, SPEC_DECISIONS 22.1, 22.7, 23 a and b).

Every malformed input is a 422 VALIDATION_ERROR naming a contract field and an issue of
the one vocabulary. Nothing is a 500, nothing the client sent is echoed back.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
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
from onepledge_contract_fakes.validation import Issue

pytestmark = pytest.mark.contract

CANARY = "ZQXJ7CANARY9"
VOCABULARY = {issue.value for issue in Issue}
# Query integers the review and SPEC_DECISIONS 23 (a) name, with the issue each must give.
BAD_INTEGERS = [
    ("9223372036854775808", "INVALID_INTEGER"),
    ("-1", "OUT_OF_RANGE"),
    ("+1", "INVALID_INTEGER"),
    ("%201", "INVALID_INTEGER"),
    ("1.0", "INVALID_INTEGER"),
    ("1e3", "INVALID_INTEGER"),
    ("%C2%B2", "INVALID_INTEGER"),
    ("%D9%A3", "INVALID_INTEGER"),
    ("", "INVALID_INTEGER"),
    ("abc", "INVALID_INTEGER"),
    ("0", "OUT_OF_RANGE"),
    ("201", "OUT_OF_RANGE"),
]


def code(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def item(name: str = "a") -> dict[str, Any]:
    return {
        "item_ref": str(uuid.uuid4()),
        "asset_token": code("asset" + name),
        "field_tokens": {kind: code(kind + name) for kind in FIELD_KINDS},
    }


def submission(**changes: object) -> dict[str, Any]:
    return {"schema_version": 1, "kind": "PLEDGE", "key_version": "1.1", "items": [item()], **changes}


@pytest.fixture
def registry() -> Iterator[tuple[AsgiTestClient, dict[str, str]]]:
    members, keys = new_members(1)
    app = create_registry_app(registry_config(members, new_api_key()), FixedClock())
    with AsgiTestClient(app) as client:
        yield client, {"X-API-Key": keys[0]}


@pytest.fixture
def key_holder() -> Iterator[tuple[AsgiTestClient, dict[str, str]]]:
    members, keys = new_members(1)
    with AsgiTestClient(create_key_holder_app(key_holder_config("kh1", members), FixedClock())) as client:
        yield client, {"X-API-Key": keys[0]}


def refused(response: httpx.Response) -> list[tuple[str, str]]:
    """The details of a 422, after checking the envelope, the vocabulary and the absence of the canary."""
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert CANARY not in response.text
    details = [(d["field"], d["issue"]) for d in error["details"]]
    assert {issue for _, issue in details} <= VOCABULARY
    return details


def post(client: AsgiTestClient, auth: dict[str, str], body: object) -> httpx.Response:
    headers = {**auth, "Idempotency-Key": code(str(uuid.uuid4()))}
    return client.post("/v1/submissions", json=body, headers=headers)


@pytest.mark.parametrize("value", [1.0, True, "1", None, [1]])
def test_schema_version_of_the_wrong_type_is_invalid_type(
    registry: tuple[AsgiTestClient, dict[str, str]], value: object
) -> None:
    client, auth = registry
    assert refused(post(client, auth, submission(schema_version=value))) == [
        ("schema_version", "INVALID_TYPE")
    ]


def test_schema_version_other_than_one_is_invalid_value(
    registry: tuple[AsgiTestClient, dict[str, str]],
) -> None:
    client, auth = registry
    assert refused(post(client, auth, submission(schema_version=2))) == [("schema_version", "INVALID_VALUE")]


def test_a_float_anywhere_is_refused_before_hashing(registry: tuple[AsgiTestClient, dict[str, str]]) -> None:
    client, auth = registry
    nested = item()
    nested["field_tokens"]["VIN"] = 1.5
    assert refused(post(client, auth, submission(items=[nested]))) == [
        ("items[0].field_tokens.VIN", "INVALID_TYPE")
    ]
    assert refused(post(client, auth, submission(key_version=1.1))) == [("key_version", "INVALID_TYPE")]
    raw = client.post_raw(
        "/v1/submissions",
        b'{"schema_version": 1e0, "kind": "PLEDGE", "key_version": "1.1", "items": []}',
        headers={**auth, "Idempotency-Key": code("raw"), "content-type": "application/json"},
    )
    assert ("schema_version", "INVALID_TYPE") in refused(raw)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"kind": 1}, ("kind", "INVALID_TYPE")),
        ({"kind": "LEASE"}, ("kind", "INVALID_VALUE")),
        ({"items": []}, ("items", "TOO_FEW_ITEMS")),
        ({"items": [item(str(n)) for n in range(501)]}, ("items", "TOO_MANY_ITEMS")),
        ({"items": [{**item(), "item_ref": "not-a-uuid"}]}, ("items[0].item_ref", "PATTERN_MISMATCH")),
        ({"items": [{**item(), "item_ref": 7}]}, ("items[0].item_ref", "INVALID_TYPE")),
        ({"items": [{**item(), "asset_token": "A" * 64}]}, ("items[0].asset_token", "PATTERN_MISMATCH")),
        ({"key_version": "v1"}, ("key_version", "PATTERN_MISMATCH")),
    ],
)
def test_every_submission_field_is_checked_by_type_and_shape(
    registry: tuple[AsgiTestClient, dict[str, str]], change: dict[str, Any], expected: tuple[str, str]
) -> None:
    client, auth = registry
    assert refused(post(client, auth, submission(**change))) == [expected]


def test_a_missing_field_is_required(registry: tuple[AsgiTestClient, dict[str, str]]) -> None:
    client, auth = registry
    body = submission()
    del body["kind"]
    assert refused(post(client, auth, body)) == [("kind", "REQUIRED")]


def test_unknown_keys_are_never_named(registry: tuple[AsgiTestClient, dict[str, str]]) -> None:
    client, auth = registry
    nested = {**item(), CANARY: 1}
    nested["field_tokens"][CANARY + "X"] = "x"
    details = refused(post(client, auth, submission(items=[nested], **{CANARY + "TOP": 1.5})))
    assert details == [("body", "UNKNOWN_FIELD")]


@pytest.mark.parametrize("body", [b"{not json", b"[1, 2]", b'"text"', b"NaN"])
def test_a_body_that_is_not_a_json_object_is_refused(
    registry: tuple[AsgiTestClient, dict[str, str]], body: bytes
) -> None:
    client, auth = registry
    response = client.post_raw(
        "/v1/submissions",
        body,
        headers={**auth, "Idempotency-Key": code("b"), "content-type": "application/json"},
    )
    assert refused(response)[0][0] == "body"


@pytest.mark.parametrize(
    ("position", "issue"), [(1, "INVALID_TYPE"), ("MAYBE", "INVALID_VALUE"), (None, "INVALID_TYPE")]
)
def test_case_positions_are_strict(
    registry: tuple[AsgiTestClient, dict[str, str]], position: object, issue: str
) -> None:
    client, auth = registry
    response = client.post(f"/v1/cases/{uuid.uuid4()}/resolution", json={"position": position}, headers=auth)
    assert refused(response) == [("position", issue)]
    canary = client.post(
        f"/v1/cases/{uuid.uuid4()}/resolution", json={"position": "DATA_ERROR", CANARY: 1}, headers=auth
    )
    assert refused(canary) == [("body", "UNKNOWN_FIELD")]


@pytest.mark.parametrize(("text", "issue"), BAD_INTEGERS)
@pytest.mark.parametrize("route", ["/v1/outbox", "/v1/cases"])
def test_query_integers_are_ascii_digits_inside_their_bounds(
    registry: tuple[AsgiTestClient, dict[str, str]], route: str, text: str, issue: str
) -> None:
    client, auth = registry
    assert refused(client.get(f"{route}?limit={text}", headers=auth)) == [("limit", issue)]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("/v1/cases?offset=1000001", ("offset", "OUT_OF_RANGE")),
        ("/v1/cases?offset=-1", ("offset", "OUT_OF_RANGE")),
        ("/v1/cases?status=SOMETHING", ("status", "INVALID_VALUE")),
        ("/v1/cases?limit=1&limit=2", ("limit", "DUPLICATE")),
        (f"/v1/cases?{CANARY}=1", ("query", "UNKNOWN_PARAMETER")),
        ("/v1/ledger/events?after_seq=-1", ("after_seq", "OUT_OF_RANGE")),
        ("/v1/ledger/events?after_seq=9223372036854775808", ("after_seq", "INVALID_INTEGER")),
        ("/v1/ledger/events?limit=1001", ("limit", "OUT_OF_RANGE")),
        ("/v1/ledger/events?view=everything", ("view", "INVALID_VALUE")),
        (f"/v1/checkpoints/latest?{CANARY}=x", ("query", "UNKNOWN_PARAMETER")),
        (f"/v1/certificates/{uuid.uuid4()}?{CANARY}=x", ("query", "UNKNOWN_PARAMETER")),
    ],
)
def test_other_query_parameters(
    registry: tuple[AsgiTestClient, dict[str, str]], query: str, expected: tuple[str, str]
) -> None:
    client, auth = registry
    assert refused(client.get(query, headers=auth)) == [expected]


def test_valid_query_boundaries_are_accepted(registry: tuple[AsgiTestClient, dict[str, str]]) -> None:
    client, auth = registry
    for query in (
        "/v1/outbox?limit=1",
        "/v1/outbox?limit=200",
        "/v1/cases?limit=200&offset=1000000&status=OPEN",
        "/v1/ledger/events?after_seq=0&limit=1000&view=headers",
    ):
        assert client.get(query, headers=auth).status_code == 200, query


def test_a_submission_with_an_unknown_query_parameter_is_refused(
    registry: tuple[AsgiTestClient, dict[str, str]],
) -> None:
    client, auth = registry
    headers = {**auth, "Idempotency-Key": code("q")}
    response = client.post(f"/v1/submissions?{CANARY}=1", json=submission(), headers=headers)
    assert refused(response) == [("query", "UNKNOWN_PARAMETER")]


def element() -> str:
    return base64.b64encode(bytes(oprf.data.hash(b"p"))).decode()


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"key_version": 1.0}, ("key_version", "INVALID_TYPE")),
        ({"key_version": True}, ("key_version", "INVALID_TYPE")),
        ({"key_version": "1"}, ("key_version", "INVALID_TYPE")),
        ({"key_version": 0}, ("key_version", "OUT_OF_RANGE")),
        ({"request_id": 5}, ("request_id", "INVALID_TYPE")),
        ({"request_id": "x"}, ("request_id", "PATTERN_MISMATCH")),
        ({"elements": []}, ("elements", "TOO_FEW_ITEMS")),
        ({"elements": [1.5]}, ("elements[0]", "INVALID_TYPE")),
        ({"elements": ["short"]}, ("elements[0]", "TOO_SHORT")),
        ({"elements": ["!" * 43 + "="]}, ("elements[0]", "PATTERN_MISMATCH")),
        ({CANARY: 1}, ("body", "UNKNOWN_FIELD")),
    ],
)
def test_the_key_holder_request_is_strict(
    key_holder: tuple[AsgiTestClient, dict[str, str]], change: dict[str, Any], expected: tuple[str, str]
) -> None:
    client, auth = key_holder
    body = {"request_id": str(uuid.uuid4()), "elements": [element()], **change}
    assert expected in refused(client.post("/v1/evaluate", json=body, headers=auth))


def test_the_key_holder_refuses_unknown_query_parameters(
    key_holder: tuple[AsgiTestClient, dict[str, str]],
) -> None:
    client, auth = key_holder
    body = {"request_id": str(uuid.uuid4()), "elements": [element()]}
    assert refused(client.post(f"/v1/evaluate?{CANARY}=1", json=body, headers=auth)) == [
        ("query", "UNKNOWN_PARAMETER")
    ]


def test_health_refuses_unknown_query_parameters(
    registry: tuple[AsgiTestClient, dict[str, str]], key_holder: tuple[AsgiTestClient, dict[str, str]]
) -> None:
    for client, _ in (registry, key_holder):
        assert refused(client.get(f"/healthz?{CANARY}=1")) == [("query", "UNKNOWN_PARAMETER")]
        assert client.get("/healthz").status_code == 200
