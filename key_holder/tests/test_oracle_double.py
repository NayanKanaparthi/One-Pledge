"""The oracle: with the same scalar, the holder answers as layer 1's `KeyHolderDouble` does.

This is the WP2 "Test against Nayan" and the target RUBRIC KH-02 names. It is the only
place the key holder tests import `onepledge_contract_fakes` (SPEC section 10, decision
11). Elements are compared byte for byte; refusals by status, code, message and details.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import oprf
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from onepledge_key_holder.domain.identity import KeyVersion
from onepledge_key_holder.infrastructure.scalar_keyring import ScalarEvaluator, ScalarKeyring

from onepledge_contract_fakes.key_holder.app import create_key_holder_app
from onepledge_contract_fakes.key_holder.config import KeyHolderConfig
from onepledge_contract_fakes.key_holder.double import EvaluationRequest, KeyHolderDouble
from onepledge_contract_fakes.settings import MemberCredential
from onepledge_contract_fakes.testing import AsgiTestClient, FixedClock

if TYPE_CHECKING:
    from conftest import Harness

pytestmark = [pytest.mark.unit, pytest.mark.contract]

REPOSITORY = Path(__file__).resolve().parents[2]
EXAMPLE = json.loads(
    (REPOSITORY / "contracts" / "examples" / "key_holder" / "evaluate.request.json").read_text(
        encoding="utf-8"
    )
)
REQUEST_ID = "983e2beb-2d83-4d32-a3b5-83a68e98f84a"
IDENTITY = base64.b64encode(bytes(32)).decode()
NOT_A_POINT = base64.b64encode(bytes([255]) * 32).decode()
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
MEMBER = "5d6f3c0e-8a7b-4c1d-9e2f-0a1b2c3d4e5f"


def double_for(scalar: bytes, key_hash: str = "0" * 64, cap: int = 1_000_000) -> KeyHolderConfig:
    return KeyHolderConfig("kh1", {1: scalar}, 1, (MemberCredential(MEMBER, key_hash),), cap)


@settings(max_examples=40, deadline=None)
@given(st.lists(st.binary(min_size=1, max_size=40), min_size=1, max_size=8))
def test_property_the_same_scalar_gives_the_double_s_elements_byte_for_byte(inputs: list[bytes]) -> None:
    scalar = bytes(oprf.mask.random())
    double = KeyHolderDouble(double_for(scalar), FixedClock())
    blinded = [oprf.mask.random().mask(oprf.data.hash(value)).to_base64() for value in inputs]
    expected = double.evaluate(MEMBER, EvaluationRequest(REQUEST_ID, None, tuple(blinded))).elements
    evaluator = ScalarKeyring({KeyVersion(1): scalar}).evaluator(KeyVersion(1))
    assert isinstance(evaluator, ScalarEvaluator)
    ours = [evaluator.evaluate(base64.b64decode(element)) for element in blinded]
    assert [base64.b64encode(raw or b"").decode() for raw in ours] == list(expected)


@pytest.fixture
def pair(make_holder: Callable[..., Harness]) -> Iterator[tuple[Harness, AsgiTestClient]]:
    """The holder and the double's HTTP app with one scalar, one member key, one cap and one clock time."""
    holder = make_holder(cap=10)
    key_hash = hashlib.sha256(holder.api_key.encode()).hexdigest()
    config = double_for(holder.scalar, key_hash, cap=10)
    with AsgiTestClient(create_key_holder_app(config, FixedClock())) as double:
        yield holder, double


def send(
    pair: tuple[Harness, AsgiTestClient], raw: bytes, headers: list[tuple[str, str]]
) -> tuple[httpx.Response, httpx.Response]:
    holder, double = pair
    ours = holder.api.request("POST", "/v1/evaluate", content=raw, headers=headers)
    theirs = double.send("POST", "/v1/evaluate", content=raw, headers=headers)
    return ours, theirs


def as_json(payload: object) -> bytes:
    return json.dumps(payload).encode()


def body(elements: list[Any], **extra: object) -> dict[str, object]:
    return {"request_id": REQUEST_ID, "key_version": None, "elements": elements, **extra}


def test_the_bank_s_body_gets_the_same_200_answer(pair: tuple[Harness, AsgiTestClient]) -> None:
    headers = [("X-API-Key", pair[0].api_key), ("Content-Type", "application/json")]
    ours, theirs = send(pair, as_json(body(EXAMPLE["elements"])), headers)
    assert ours.status_code == theirs.status_code == 200
    assert ours.json() == theirs.json()
    assert ours.json()["elements"] == theirs.json()["elements"]


def non_canonical(element: str) -> str:
    return element[:42] + ALPHABET[ALPHABET.index(element[42]) + 1] + "="


GOOD = EXAMPLE["elements"][0]
REFUSALS: dict[str, bytes] = {
    "identity point": as_json(body([IDENTITY])),
    "one bad element in a batch": as_json(body([GOOD, GOOD, NOT_A_POINT, GOOD, IDENTITY])),
    "non-canonical base64": as_json(body([GOOD, non_canonical(GOOD)])),
    "unknown pinned key version": as_json(body([GOOD], key_version=9)),
    "key version out of range": as_json(body([GOOD], key_version=0)),
    "1001 elements": as_json(body([GOOD] * 1001)),
    "no elements": as_json(body([])),
    "over the daily cap": as_json(body([GOOD] * 11)),
    "a short element": as_json(body(["abc"])),
    "an element that is not text": as_json(body([5])),
    "a request id in another spelling": as_json(body([GOOD], request_id=REQUEST_ID.upper())),
    "an unknown field": as_json(body([GOOD], extra=1)),
    "missing fields": as_json({}),
    "not json": b"{",
    "a json list": b"[]",
    "body over the size limit": b"x" * 1_100_000,
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_refusal_has_the_double_s_status_code_message_and_details(
    pair: tuple[Harness, AsgiTestClient], case: str
) -> None:
    headers = [("X-API-Key", pair[0].api_key), ("Content-Type", "application/json")]
    ours, theirs = send(pair, REFUSALS[case], headers)
    assert ours.status_code == theirs.status_code >= 400
    mine, his = ours.json()["error"], theirs.json()["error"]
    assert (mine["code"], mine["message"]) == (his["code"], his["message"])
    assert mine.get("details") == his.get("details")
    assert ours.headers.get("retry-after") == theirs.headers.get("retry-after")
    assert pair[0].journal.records == []


@pytest.mark.parametrize("case", ["missing", "unknown", "empty", "duplicate"])
def test_a_missing_unknown_or_duplicate_key_is_refused_as_the_double_refuses_it(
    pair: tuple[Harness, AsgiTestClient], case: str
) -> None:
    valid = ("X-API-Key", pair[0].api_key)
    credentials: dict[str, list[tuple[str, str]]] = {
        "missing": [],
        "unknown": [("X-API-Key", "opk_unknown")],
        "empty": [("X-API-Key", "")],
        "duplicate": [valid, valid],
    }
    headers = [*credentials[case], ("Content-Type", "application/json")]
    ours, theirs = send(pair, as_json(body([GOOD])), headers)
    assert ours.status_code == theirs.status_code == 401
    assert ours.json()["error"]["code"] == theirs.json()["error"]["code"] == "UNAUTHENTICATED"
    assert ours.json()["error"]["message"] == theirs.json()["error"]["message"]


def test_the_cap_counts_and_refuses_as_the_double_does(pair: tuple[Harness, AsgiTestClient]) -> None:
    headers = [("X-API-Key", pair[0].api_key), ("Content-Type", "application/json")]
    for size, status in ((6, 200), (6, 429), (4, 200), (1, 429)):
        ours, theirs = send(pair, as_json(body([GOOD] * size)), headers)
        assert ours.status_code == theirs.status_code == status
        assert ours.headers.get("retry-after") == theirs.headers.get("retry-after")
    assert [record.item_count for record in pair[0].journal.records] == [6, 4]


def test_the_only_health_difference_is_the_test_double_flag(pair: tuple[Harness, AsgiTestClient]) -> None:
    holder, double = pair
    ours = holder.api.request("GET", "/healthz").json()
    theirs = double.get("/healthz").json()
    assert theirs.pop("test_double") is True
    assert ours == theirs
