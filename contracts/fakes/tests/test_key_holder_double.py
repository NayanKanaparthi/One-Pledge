"""The key holder double evaluates blinded points exactly as the contract says (SPEC 7.1, 9.3)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import oprf
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from onepledge_contract_fakes.errors import (
    ConfigurationError,
    InvalidElementError,
    KeyVersionUnavailableError,
    RateLimitedError,
    UnauthenticatedError,
)
from onepledge_contract_fakes.key_holder.config import KeyHolderConfig
from onepledge_contract_fakes.key_holder.double import EvaluationRequest, KeyHolderDouble
from onepledge_contract_fakes.testing import FixedClock, key_holder_config, new_members, new_scalar

pytestmark = pytest.mark.unit

REQUEST_ID = "983e2beb-2d83-4d32-a3b5-83a68e98f84a"


def b64(point: bytes) -> str:
    return base64.b64encode(point).decode("ascii")


def holder(
    scalar: bytes | None = None, cap: int = 1_000_000, holder_id: str = "kh1"
) -> tuple[KeyHolderDouble, str]:
    members, keys = new_members(1)
    config = key_holder_config(holder_id, members, scalar, daily_element_cap=cap)
    return KeyHolderDouble(config, FixedClock()), keys[0]


def evaluate(double: KeyHolderDouble, key: str, elements: list[str], version: int | None = None) -> list[str]:
    member = double.authenticate(key)
    return list(double.evaluate(member, EvaluationRequest(REQUEST_ID, version, tuple(elements))).elements)


def test_reproduces_the_published_vector(contracts_dir: Path) -> None:
    vector = json.loads((contracts_dir / "examples" / "vectors" / "tokenization.json").read_text())
    kh1, key1 = holder(base64.b64decode(vector["holder_1_scalar_b64"]))
    kh2, key2 = holder(base64.b64decode(vector["holder_2_scalar_b64"]), holder_id="kh2")
    blinded = [t["blinded"] for t in vector["tokens"]]
    after_1 = evaluate(kh1, key1, blinded)
    assert after_1 == [t["after_holder_1"] for t in vector["tokens"]]
    assert evaluate(kh2, key2, after_1) == [t["after_holder_2"] for t in vector["tokens"]]


@settings(max_examples=40, deadline=None)
@given(st.lists(st.binary(min_size=1, max_size=40), min_size=1, max_size=6))
def test_property_two_holders_in_series_equal_direct_evaluation_and_ignore_the_blind(
    inputs: list[bytes],
) -> None:
    k1, k2 = new_scalar(), new_scalar()
    kh1, key1 = holder(k1)
    kh2, key2 = holder(k2, holder_id="kh2")
    points = [oprf.data.hash(value) for value in inputs]
    results = []
    for _ in range(2):
        blinds = [oprf.mask.random() for _ in points]
        blinded = [b64(bytes(blind.mask(point))) for blind, point in zip(blinds, points, strict=True)]
        evaluated = evaluate(kh2, key2, evaluate(kh1, key1, blinded))
        unblinded = [
            bytes(blind.unmask(oprf.data.from_base64(value)))
            for blind, value in zip(blinds, evaluated, strict=True)
        ]
        results.append(unblinded)
    direct = [bytes(oprf.mask(k2).mask(oprf.mask(k1).mask(point))) for point in points]
    assert results[0] == direct
    assert results[1] == direct


def test_every_invalid_element_is_reported_by_index_and_nothing_is_returned() -> None:
    double, key = holder()
    valid = b64(bytes(oprf.data.hash(b"loan")))
    identity = b64(bytes(32))
    not_a_point = b64(bytes([255] * 32))
    with pytest.raises(InvalidElementError) as raised:
        evaluate(double, key, [valid, identity, valid, not_a_point])
    assert [d.field for d in raised.value.details] == ["elements[1]", "elements[3]"]
    assert {d.issue for d in raised.value.details} == {"NOT_A_VALID_POINT"}
    assert double.evaluation_log() == ()


def test_unknown_pinned_version_is_refused_and_the_active_one_is_used_by_default() -> None:
    double, key = holder()
    element = b64(bytes(oprf.data.hash(b"x")))
    with pytest.raises(KeyVersionUnavailableError):
        evaluate(double, key, [element], version=2)
    member = double.authenticate(key)
    result = double.evaluate(member, EvaluationRequest(REQUEST_ID, None, (element,)))
    assert result.key_version == 1 and result.holder_id == "kh1"


def test_a_second_key_version_can_be_pinned() -> None:
    members, keys = new_members(1)
    old, new = new_scalar(), new_scalar()
    config = KeyHolderConfig("kh1", {1: old, 2: new}, 2, members)
    double = KeyHolderDouble(config, FixedClock())
    point = oprf.data.hash(b"pin")
    element = b64(bytes(point))
    assert evaluate(double, keys[0], [element], version=1) == [b64(bytes(oprf.mask(old).mask(point)))]
    assert evaluate(double, keys[0], [element]) == [b64(bytes(oprf.mask(new).mask(point)))]


def test_daily_element_cap_refuses_the_whole_request_with_retry_after() -> None:
    double, key = holder(cap=3)
    element = b64(bytes(oprf.data.hash(b"cap")))
    evaluate(double, key, [element, element])
    with pytest.raises(RateLimitedError) as raised:
        evaluate(double, key, [element, element])
    assert raised.value.retry_after_seconds is not None and raised.value.retry_after_seconds >= 1
    assert double.stats()["total"] == {"requests": 1, "elements": 2}


def test_unknown_or_missing_keys_are_refused() -> None:
    double, _ = holder()
    for key in (None, "", "opk_unknown"):
        with pytest.raises(UnauthenticatedError):
            double.authenticate(key)


def test_log_records_counts_and_never_elements() -> None:
    double, key = holder()
    element = b64(bytes(oprf.data.hash(b"log")))
    evaluate(double, key, [element])
    record = double.evaluation_log()[0]
    assert record.element_count == 1 and record.request_id == REQUEST_ID
    assert element not in repr(record)


@pytest.mark.parametrize("scalar", [bytes(32), b"short"])
def test_zero_or_short_scalars_stop_startup(scalar: bytes) -> None:
    members, _ = new_members(1)
    with pytest.raises(ConfigurationError):
        KeyHolderDouble(KeyHolderConfig("kh1", {1: scalar}, 1, members), FixedClock())


def test_active_version_must_exist() -> None:
    members, _ = new_members(1)
    with pytest.raises(ConfigurationError):
        KeyHolderDouble(KeyHolderConfig("kh1", {1: new_scalar()}, 2, members), FixedClock())
