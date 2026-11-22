"""Idempotency key derivation (SPEC 13.4): the formula, and a different key whenever any input differs."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import fields, replace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from onepledge_bank_agent.domain.enums import RequestOrigin, TapeKind
from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.idempotency import (
    IdempotencyInputs,
    items_digest,
    resubmission_batch_part,
    tape_batch_part,
)

pytestmark = pytest.mark.unit

TOKENS = ["a" * 64, "b" * 64, "c" * 64]
MEMBER, SUBMISSION = uuid.UUID(int=1, version=4), uuid.UUID(int=2, version=4)


def test_the_key_is_the_spec_formula_computed_independently() -> None:
    digest = hashlib.sha256("\n".join(TOKENS).encode()).hexdigest()
    text = f"onepledge:idem:v1|{MEMBER}|{SUBMISSION}|TAPE|PLEDGE|3|{digest}"
    inputs = IdempotencyInputs(
        MEMBER, SUBMISSION, RequestOrigin.TAPE, TapeKind.PLEDGE, tape_batch_part(3), items_digest(TOKENS)
    )
    assert inputs.key() == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_a_resubmission_uses_the_sorted_cleared_case_ids() -> None:
    first, second = uuid.UUID(int=9, version=4), uuid.UUID(int=3, version=4)
    assert resubmission_batch_part([first, second]) == f"case:{second},{first}"
    assert resubmission_batch_part([second, first]) == resubmission_batch_part([first, second])
    with pytest.raises(InvalidValueError):
        resubmission_batch_part([])


def test_a_negative_or_boolean_batch_index_is_refused() -> None:
    for bad in (-1, True):
        with pytest.raises(InvalidValueError):
            tape_batch_part(bad)


def test_the_items_digest_depends_on_the_position_order() -> None:
    assert items_digest(TOKENS) != items_digest(list(reversed(TOKENS)))


INPUTS = st.builds(
    IdempotencyInputs,
    st.uuids(version=4),
    st.uuids(version=4),
    st.sampled_from(list(RequestOrigin)),
    st.sampled_from(list(TapeKind)),
    st.integers(0, 10_000).map(tape_batch_part),
    st.lists(st.text("0123456789abcdef", min_size=64, max_size=64), min_size=1, max_size=4).map(items_digest),
)
CHANGES = {
    "member_id": st.uuids(version=4),
    "submission_id": st.uuids(version=4),
    "origin": st.sampled_from(list(RequestOrigin)),
    "kind": st.sampled_from(list(TapeKind)),
    "batch_part": st.integers(0, 10_000).map(tape_batch_part),
    "items_digest": st.text("0123456789abcdef", min_size=64, max_size=64),
}


@given(INPUTS, st.sampled_from(sorted(CHANGES)), st.data())
def test_keys_differ_when_any_input_differs(
    inputs: IdempotencyInputs, name: str, data: st.DataObject
) -> None:
    assert {field.name for field in fields(IdempotencyInputs)} == set(CHANGES)
    other = data.draw(CHANGES[name].filter(lambda value: value != getattr(inputs, name)))
    change: dict[str, Any] = {name: other}
    changed = replace(inputs, **change)
    assert changed.key() != inputs.key()
    assert inputs.key() == replace(inputs).key() and len(inputs.key()) == 64
