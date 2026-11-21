"""Idempotency, validation, key version, caps and the race of the registry double."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid

import pytest

from onepledge_contract_fakes.errors import (
    IdempotencyKeyReusedError,
    KeyVersionMismatchError,
    RateLimitedError,
    ValidationFailedError,
)
from onepledge_contract_fakes.registry.double import RegistryDouble
from onepledge_contract_fakes.registry.model import FIELD_KINDS, Submission, SubmissionItem, SubmissionKind
from onepledge_contract_fakes.testing import (
    CountingIds,
    FixedClock,
    new_api_key,
    new_members,
    registry_config,
)

pytestmark = pytest.mark.unit

RACE_ROUNDS = 100


def code(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def item(name: str, with_fields: bool = True) -> SubmissionItem:
    fields = {k: code(k + name) for k in FIELD_KINDS} if with_fields else None
    return SubmissionItem(str(uuid.uuid4()), code("asset" + name), fields)


def double(cap: int = 1_000_000) -> tuple[RegistryDouble, list[str]]:
    members, _ = new_members(2)
    registry = RegistryDouble(registry_config(members, new_api_key(), cap), FixedClock(), CountingIds())
    return registry, [m.member_id for m in members]


def pledge(
    *items: SubmissionItem, version: str = "1.1", kind: SubmissionKind = SubmissionKind.PLEDGE
) -> Submission:
    return Submission(kind, version, items)


def test_same_key_and_body_replays_the_stored_bytes() -> None:
    registry, ids = double()
    submission = pledge(item("a"), item("b"))
    first = registry.submit(ids[0], code("key"), submission, code("body"))
    again = registry.submit(ids[0], code("key"), submission, code("body"))
    assert first.status == 201 and not first.replay
    assert again.status == 200 and again.replay and again.body == first.body
    stats = registry.test_controls.stats()["total"]
    assert isinstance(stats, dict) and stats["submissions"] == 1 and stats["active_locks"] == 2


def test_same_key_with_another_body_is_refused() -> None:
    registry, ids = double()
    registry.submit(ids[0], code("key"), pledge(item("a")), code("body-1"))
    with pytest.raises(IdempotencyKeyReusedError):
        registry.submit(ids[0], code("key"), pledge(item("b")), code("body-2"))


def test_keys_are_scoped_per_member() -> None:
    registry, ids = double()
    registry.submit(ids[0], code("key"), pledge(item("a")), code("body"))
    result = registry.submit(ids[1], code("key"), pledge(item("b")), code("other"))
    assert result.status == 201


def test_a_stale_key_version_is_refused_and_not_stored() -> None:
    registry, ids = double()
    with pytest.raises(KeyVersionMismatchError):
        registry.submit(ids[0], code("key"), pledge(item("a"), version="2.1"), code("body"))
    assert registry.submit(ids[0], code("key"), pledge(item("a")), code("body")).status == 201


@pytest.mark.parametrize(
    ("submission", "issue"),
    [
        (pledge(item("a"), item("a")), "DUPLICATE_ASSET_TOKEN"),
        (pledge(item("a", with_fields=False)), "REQUIRED"),
        (pledge(item("a"), kind=SubmissionKind.RELEASE), "INVALID_VALUE"),
    ],
)
def test_semantic_validation(submission: Submission, issue: str) -> None:
    registry, ids = double()
    with pytest.raises(ValidationFailedError) as raised:
        registry.submit(ids[0], code("key"), submission, code("body"))
    assert issue in {d.issue for d in raised.value.details}


def test_duplicate_item_refs_are_refused() -> None:
    registry, ids = double()
    first = item("a")
    duplicate = SubmissionItem(first.item_ref, code("other"), first.field_tokens)
    with pytest.raises(ValidationFailedError) as raised:
        registry.submit(ids[0], code("key"), pledge(first, duplicate), code("body"))
    assert {d.issue for d in raised.value.details} == {"DUPLICATE"}


def test_daily_item_cap() -> None:
    registry, ids = double(cap=3)
    registry.submit(ids[0], code("k1"), pledge(item("a"), item("b")), code("b1"))
    with pytest.raises(RateLimitedError):
        registry.submit(ids[0], code("k2"), pledge(item("c"), item("d")), code("b2"))
    assert registry.submit(ids[1], code("k3"), pledge(item("c"), item("d")), code("b3")).status == 201


def race_once(round_number: int) -> tuple[list[str], RegistryDouble]:
    """Two members submit the same asset code, released together by a barrier."""
    registry, ids = double()
    contested = item(f"race-{round_number}")
    barrier = threading.Barrier(2)
    results: dict[int, str] = {}

    def submit(member: int) -> None:
        barrier.wait()
        outcome = registry.submit(ids[member], code(f"k{member}"), pledge(contested), code(f"b{member}"))
        results[member] = json.loads(outcome.body)["results"][0]["result"]

    threads = [threading.Thread(target=submit, args=(member,)) for member in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return sorted(results.values()), registry


def test_race_concurrent_pledges_of_one_asset_lock_exactly_once_every_time() -> None:
    for round_number in range(RACE_ROUNDS):
        outcomes, registry = race_once(round_number)
        assert outcomes == ["CONFLICT", "LOCKED"], f"round {round_number}"
        stats = registry.test_controls.stats()["total"]
        assert isinstance(stats, dict) and stats["active_locks"] == 1 and stats["open_cases"] == 1
