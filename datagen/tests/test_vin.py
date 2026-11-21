"""ISO 3779 check digits (SPEC 5.5), including the properties of SPEC 21.1."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from onepledge_datagen.domain.vin import (
    ALPHABET,
    TRANSLITERATION,
    WEIGHTS,
    check_digit_for,
    different_value_characters,
    is_valid_vin,
    replace_at,
    same_value_characters,
    with_check_digit,
)

pytestmark = pytest.mark.unit

vin_drafts = st.text(alphabet=ALPHABET, min_size=17, max_size=17).map(with_check_digit)
positions = st.integers(min_value=0, max_value=16).filter(lambda index: index != 8)


def test_published_vector_is_valid() -> None:
    assert is_valid_vin("1HGCM82633A004352")
    assert check_digit_for("1HGCM82633A004352") == "3"


def test_remainder_ten_is_written_as_x() -> None:
    assert is_valid_vin("1M8GDM9AXKP042788")
    assert with_check_digit("1M8GDM9A0KP042788")[8] == "X"


@pytest.mark.parametrize(
    "text",
    ["1HGCM82633A004353", "1HGCM82633A00435", "1HGCM82633A0043521", "1HGCM82633A00435O", "1hgcm82633a004352"],
)
def test_invalid_vins(text: str) -> None:
    assert not is_valid_vin(text)


def test_tables_match_the_spec() -> None:
    assert len(ALPHABET) == 33 and not set("IOQ") & set(ALPHABET)
    assert WEIGHTS[8] == 0 and sum(WEIGHTS) == 89
    assert TRANSLITERATION["P"] == 7 and TRANSLITERATION["R"] == 9 and TRANSLITERATION["S"] == 2


@given(vin_drafts)
def test_property_a_generated_valid_vin_parses(vin: str) -> None:
    assert is_valid_vin(vin)


@given(vin_drafts, positions, st.data())
def test_property_a_different_value_change_always_fails(vin: str, index: int, data: st.DataObject) -> None:
    replacement = data.draw(st.sampled_from(different_value_characters(vin[index])))
    assert not is_valid_vin(replace_at(vin, index, replacement))


@given(vin_drafts, positions, st.data())
def test_property_a_same_value_substitution_always_passes(vin: str, index: int, data: st.DataObject) -> None:
    options = same_value_characters(vin[index])
    if not options:
        assert TRANSLITERATION[vin[index]] == 0
        return
    assert is_valid_vin(replace_at(vin, index, data.draw(st.sampled_from(options))))
