"""`EvaluateBlinded` and `AuthenticateMember`: every WP2 edge case at the use case, no HTTP, no database."""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

import oprf
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from onepledge_key_holder.application.evaluate_blinded import EvaluateBlinded, HolderKeys
from onepledge_key_holder.domain.errors import (
    InvalidElementError,
    KeyVersionUnavailableError,
    RateLimitedError,
    UnauthenticatedError,
)
from onepledge_key_holder.domain.evaluation import EvaluationRequest
from onepledge_key_holder.domain.identity import HolderId, KeyVersion
from onepledge_key_holder.infrastructure.scalar_keyring import (
    ScalarEvaluator,
    ScalarKeyring,
    UnusableScalarError,
)

if TYPE_CHECKING:
    from conftest import Harness

pytestmark = pytest.mark.unit

REQUEST_ID = "983e2beb-2d83-4d32-a3b5-83a68e98f84a"
IDENTITY = base64.b64encode(bytes(32)).decode()
NOT_A_POINT = base64.b64encode(bytes([255]) * 32).decode()


def request(elements: list[str], version: int | None = None) -> EvaluationRequest:
    pinned = KeyVersion(version) if version is not None else None
    return EvaluationRequest(REQUEST_ID, pinned, tuple(elements))


def test_each_element_is_the_scalar_times_the_point_in_request_order(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder(holder_id="kh2")
    seeds = [b"a", b"b", b"c"]
    result = holder.use_cases.evaluate.run(holder.member_id, request([point(seed) for seed in seeds]))
    mask = oprf.mask(holder.scalar)
    assert list(result.elements) == [mask.mask(oprf.data.hash(seed)).to_base64() for seed in seeds]
    assert (result.request_id, result.holder_id, result.key_version) == (
        REQUEST_ID,
        HolderId.KH2,
        KeyVersion(1),
    )


def test_the_identity_point_rejects_the_request(make_holder: Callable[..., Harness]) -> None:
    holder = make_holder()
    with pytest.raises(InvalidElementError) as raised:
        holder.run([IDENTITY])
    assert raised.value.indexes == (0,)
    assert holder.journal.records == [] and holder.journal.used == {}


def test_one_bad_element_in_a_batch_of_good_ones_rejects_all_and_stores_nothing(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder(cap=5)
    good = point(b"loan")
    with pytest.raises(InvalidElementError) as raised:
        holder.run([good, IDENTITY, good, NOT_A_POINT, good])
    assert raised.value.indexes == (1, 3)
    assert holder.journal.records == []
    # The refused request did not use the cap: five good elements still fit.
    assert len(holder.run([good] * 5)) == 5


def test_non_canonical_base64_is_an_invalid_element(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    good = point(b"loan")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    spelled = good[:42] + alphabet[alphabet.index(good[42]) + 1] + "="
    with pytest.raises(InvalidElementError) as raised:
        holder.run([good, spelled])
    assert raised.value.indexes == (1,)


class ZeroEvaluator:
    """An evaluator whose result is the identity, as a zero scalar's would be."""

    def evaluate(self, point: bytes) -> bytes | None:
        del point
        return bytes(32)


class ZeroKeyring:
    def evaluator(self, version: KeyVersion) -> ZeroEvaluator:
        del version
        return ZeroEvaluator()


def test_an_all_zero_result_rejects_the_request(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    use_case = EvaluateBlinded(
        HolderKeys(HolderId.KH1, KeyVersion(1), ZeroKeyring()), holder.journal, holder.clock, _Ids(), 10
    )
    with pytest.raises(InvalidElementError) as raised:
        use_case.run(holder.member_id, request([point(b"x"), point(b"y")]))
    assert raised.value.indexes == (0, 1)
    assert holder.journal.records == []


class _Ids:
    def new_id(self) -> uuid.UUID:
        return uuid.uuid4()


def test_an_unknown_pinned_version_is_unavailable_and_null_means_the_active_one(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder(version=3)
    element = point(b"pin")
    with pytest.raises(KeyVersionUnavailableError):
        holder.run([element], version=2)
    assert holder.journal.records == []
    pinned = holder.use_cases.evaluate.run(holder.member_id, request([element], version=3))
    default = holder.use_cases.evaluate.run(holder.member_id, request([element]))
    assert pinned.key_version == default.key_version == KeyVersion(3)
    assert pinned.elements == default.elements


def test_the_daily_cap_refuses_the_whole_request_with_retry_after(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder(cap=3)
    element = point(b"cap")
    holder.run([element, element])
    with pytest.raises(RateLimitedError) as raised:
        holder.run([element, element])
    assert raised.value.retry_after_seconds == 43_200
    assert [record.item_count for record in holder.journal.records] == [2]
    # One more still fits, and the next UTC day starts a new window.
    holder.run([element])
    with pytest.raises(RateLimitedError):
        holder.run([element])
    holder.clock.advance(43_200)
    assert len(holder.run([element, element, element])) == 3


def test_a_second_call_with_the_same_points_returns_the_same_elements(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    elements = [point(b"one"), point(b"two")]
    assert holder.run(elements) == holder.run(elements)
    # Decision 2: the request id is recorded, not an idempotency store. Both calls are logged.
    assert [record.request_id for record in holder.journal.records] == [REQUEST_ID, REQUEST_ID]


def test_the_log_has_a_row_and_no_element_bytes(
    make_holder: Callable[..., Harness], point: Callable[[bytes], str]
) -> None:
    holder = make_holder()
    element = point(b"log")
    evaluated = holder.run([element])[0]
    (record,) = holder.journal.records
    assert (record.member_id, record.request_id, record.item_count, record.at) == (
        holder.member_id,
        REQUEST_ID,
        1,
        holder.clock.now(),
    )
    shown = repr(record)
    for text in (element, evaluated, base64.b64decode(element).hex(), base64.b64decode(evaluated).hex()):
        assert text not in shown
    assert set(vars(record)) == {"eval_id", "member_id", "request_id", "item_count", "at"}


@settings(max_examples=25, deadline=None)
@given(st.lists(st.binary(min_size=1, max_size=40), min_size=1, max_size=5))
def test_property_two_holders_in_series_then_unblinding_is_k2_k1_p(inputs: list[bytes]) -> None:
    k1, k2 = bytes(oprf.mask.random()), bytes(oprf.mask.random())
    first, second = ScalarEvaluator(k1), ScalarEvaluator(k2)
    for value in inputs:
        hashed = oprf.data.hash(value)
        blind = oprf.mask.random()
        after_1 = first.evaluate(bytes(blind.mask(hashed)))
        assert after_1 is not None
        after_2 = second.evaluate(after_1)
        assert after_2 is not None
        unblinded = bytes(blind.unmask(oprf.data(after_2)))
        assert unblinded == bytes(oprf.mask(k2).mask(oprf.mask(k1).mask(hashed)))


@pytest.mark.parametrize("scalar", [bytes(32), b"short", bytes(33)])
def test_a_scalar_that_is_not_32_bytes_or_is_all_zeros_is_refused(scalar: bytes) -> None:
    with pytest.raises(UnusableScalarError):
        ScalarKeyring({KeyVersion(1): scalar})


def test_the_keyring_never_shows_a_scalar() -> None:
    scalar = bytes(oprf.mask.random())
    keyring = ScalarKeyring({KeyVersion(1): scalar})
    shown = repr(keyring) + repr(keyring.evaluator(KeyVersion(1)))
    assert scalar.hex() not in shown and base64.b64encode(scalar).decode() not in shown
    assert keyring.evaluator(KeyVersion(2)) is None


def test_a_point_the_library_refuses_is_none() -> None:
    assert ScalarEvaluator(bytes(oprf.mask.random())).evaluate(b"short") is None


@pytest.mark.parametrize("api_key", [None, "", "opk_unknown"])
def test_a_missing_or_unknown_key_is_unauthenticated(
    make_holder: Callable[..., Harness], api_key: str | None
) -> None:
    holder = make_holder()
    with pytest.raises(UnauthenticatedError):
        holder.use_cases.authenticate.run(api_key)
    assert holder.use_cases.authenticate.run(holder.api_key) == holder.member_id
