"""Canonical JSON rule of SPEC 4 and registry.yaml."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from onepledge_contract_fakes.canonical_json import JsonValue, canonical_json, canonical_sha256

pytestmark = pytest.mark.unit

json_scalars: st.SearchStrategy[JsonValue] = st.one_of(
    st.none(), st.booleans(), st.integers(), st.text(max_size=20)
)
json_values: st.SearchStrategy[JsonValue] = st.recursive(
    json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4), st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=20,
)


def test_sorts_keys_without_whitespace_and_keeps_non_ascii() -> None:
    assert canonical_json({"b": 1, "a": [True, None, "é"]}) == '{"a":[true,null,"é"],"b":1}'.encode()


def test_rejects_floats_anywhere() -> None:
    with pytest.raises(TypeError):
        canonical_json(json.loads('{"a": [1, {"b": 1.5}]}'))


def test_sha256_of_the_vector_payload() -> None:
    payload = {"member_id": "b22cfcf4-0e9d-5315-8781-283c4872ae4e"}
    assert canonical_sha256(payload) == "d55b66bcdd2ee00c4dacf770ca21203e9e9c5da7e369c8c1f42afe96348f3245"


@given(st.dictionaries(st.text(max_size=8), json_values, max_size=6))
def test_property_invariant_under_key_order(document: dict[str, JsonValue]) -> None:
    reordered = dict(reversed(list(document.items())))
    assert canonical_json(reordered) == canonical_json(document)
    assert json.loads(canonical_json(document)) == document
