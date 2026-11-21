"""`Vin` and the ISO 3779 check digit (SPEC 5.5, 8.1), with the properties of SPEC 21.1."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from onepledge_bank_agent.domain import Vin
from onepledge_bank_agent.domain.errors import InvalidVinError
from onepledge_bank_agent.domain.vin import VinDefect, check_digit, transliterate, vin_defect

pytestmark = pytest.mark.unit

ALPHABET = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"
# Transliteration written out from SPEC 5.5, independent of the product table.
VALUE = {**{d: int(d) for d in "0123456789"}, **dict(zip("ABCDEFGH", range(1, 9), strict=True))}
VALUE.update({"J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9})
VALUE.update(dict(zip("STUVWXYZ", range(2, 10), strict=True)))
WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)


def with_check_digit(sixteen: str) -> str:
    """Insert the SPEC 5.5 check digit at position 9 of 16 other characters."""
    body = sixteen[:8] + "0" + sixteen[8:]
    total = sum(VALUE[c] * w for c, w in zip(body, WEIGHTS, strict=True))
    digit = "X" if total % 11 == 10 else str(total % 11)
    return sixteen[:8] + digit + sixteen[8:]


VINS = st.text(alphabet=ALPHABET, min_size=16, max_size=16).map(with_check_digit)


def test_the_spec_vector_is_valid_and_its_repr_is_masked() -> None:
    vin = Vin.parse("1HGCM82633A004352")
    assert vin.value == "1HGCM82633A004352"
    assert repr(vin) == "Vin(***)" and str(vin) == "Vin(***)"
    assert "1HGCM" not in f"{vin} {vin!r} {[vin]}"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("1HGCM82633A00435", VinDefect.LENGTH),
        ("1HGCM82633A0043521", VinDefect.LENGTH),
        ("", VinDefect.LENGTH),
        ("1HGCM82633A00435O", VinDefect.CHARACTER),
        ("1hgcm82633a004352", VinDefect.CHARACTER),
        ("1HGCM82633A00435-", VinDefect.CHARACTER),
        ("1HGCM82633A004353", VinDefect.CHECK_DIGIT),
        ("1HGCM82643A004352", VinDefect.CHECK_DIGIT),
    ],
)
def test_every_defect_raises_with_its_reason_and_never_the_value(text: str, reason: VinDefect) -> None:
    with pytest.raises(InvalidVinError) as raised:
        Vin.parse(text)
    assert raised.value.reason == reason.value
    assert raised.value.code == "INVALID_VALUE"
    if text:
        assert text not in str(raised.value) and text not in repr(raised.value.args)


def test_a_check_value_of_ten_is_written_x() -> None:
    candidates = (with_check_digit(f"1M8GDM9A{n:08d}"[:16]) for n in range(200))
    with_x = next(v for v in candidates if v[8] == "X")
    assert check_digit(with_x) == "X"
    assert vin_defect(with_x) is None


def test_transliteration_follows_the_spec_table() -> None:
    assert {c: transliterate(c) for c in ALPHABET} == VALUE


@given(VINS)
def test_a_generated_valid_vin_parses(text: str) -> None:
    assert Vin.parse(text).value == text


@given(VINS, st.integers(min_value=0, max_value=16), st.sampled_from(ALPHABET))
def test_changing_one_character_to_a_different_value_always_fails(text: str, index: int, char: str) -> None:
    if index == 8 or VALUE[char] == VALUE[text[index]]:
        return
    changed = text[:index] + char + text[index + 1 :]
    assert vin_defect(changed) is VinDefect.CHECK_DIGIT


@given(VINS, st.integers(min_value=0, max_value=16), st.sampled_from(ALPHABET))
def test_a_same_value_substitution_always_passes(text: str, index: int, char: str) -> None:
    if index == 8 or VALUE[char] != VALUE[text[index]]:
        return
    changed = text[:index] + char + text[index + 1 :]
    assert Vin.parse(changed).value == changed
