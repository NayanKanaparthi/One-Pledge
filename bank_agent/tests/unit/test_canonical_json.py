"""Canonical JSON of SPEC 4: exact bytes, no floats, invariant under key order."""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from onepledge_bank_agent.domain.canonical_json import canonical_json, canonical_sha256
from onepledge_bank_agent.domain.errors import DomainError, NonCanonicalValueError

pytestmark = pytest.mark.unit


def test_bytes_are_sorted_compact_utf8_and_unescaped() -> None:
    value = {"b": [1, True, None], "a": "é", "c": {"z": 1, "y": "x"}}
    assert canonical_json(value) == '{"a":"é","b":[1,true,null],"c":{"y":"x","z":1}}'.encode()
    assert canonical_sha256(value) == hashlib.sha256(canonical_json(value)).hexdigest()


@pytest.mark.parametrize("value", [1.5, {"a": 0.0}, [1, [2.5]], {"a": {"b": float("nan")}}])
def test_floats_are_refused_anywhere(value: object) -> None:
    with pytest.raises(TypeError) as raised:
        canonical_json(value)
    assert isinstance(raised.value, NonCanonicalValueError) and isinstance(raised.value, DomainError)
    assert (raised.value.field, raised.value.issue) == ("canonical_json", "INVALID_TYPE")


def test_non_string_keys_are_refused() -> None:
    with pytest.raises(NonCanonicalValueError) as raised:
        canonical_json({1: "a"})
    assert raised.value.issue == "INVALID_TYPE"


JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=8),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=6), children, max_size=4)
    ),
    max_leaves=12,
)


@given(st.dictionaries(st.text(max_size=6), JSON, max_size=6))
def test_canonical_json_is_invariant_under_key_order(value: dict[str, object]) -> None:
    reversed_order = dict(reversed(list(value.items())))
    assert canonical_json(value) == canonical_json(reversed_order)
