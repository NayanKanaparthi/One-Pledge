"""ISO 3779 vehicle identification numbers as used by 49 CFR 565 (SPEC 5.5).

This is the generator's own implementation; the bank agent has a separate one.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

VIN_LENGTH = 17
CHECK_MODULUS = 11
CHECK_VALUE_WRITTEN_AS_X = 10
CHECK_DIGIT_INDEX = 8
MODEL_YEAR_INDEX = 9
ALPHABET = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"
LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"

TRANSLITERATION: Mapping[str, int] = MappingProxyType(
    {
        **{str(digit): digit for digit in range(10)},
        "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
        "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
        "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
    }
)  # fmt: skip
WEIGHTS: tuple[int, ...] = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)

MODEL_YEAR_CODES: Mapping[int, str] = MappingProxyType(
    {
        2015: "F", 2016: "G", 2017: "H", 2018: "J", 2019: "K", 2020: "L",
        2021: "M", 2022: "N", 2023: "P", 2024: "R", 2025: "S", 2026: "T",
    }
)  # fmt: skip


def weighted_sum(vin: str) -> int:
    """The weighted transliteration sum of a 17 character VIN."""
    return sum(TRANSLITERATION[char] * weight for char, weight in zip(vin, WEIGHTS, strict=True))


def check_digit_for(vin: str) -> str:
    """The check digit character position 9 must hold for this VIN."""
    value = weighted_sum(vin) % CHECK_MODULUS
    return "X" if value == CHECK_VALUE_WRITTEN_AS_X else str(value)


def is_valid_vin(text: str) -> bool:
    """17 alphabet characters with a correct check digit."""
    if len(text) != VIN_LENGTH or any(char not in TRANSLITERATION for char in text):
        return False
    return text[CHECK_DIGIT_INDEX] == check_digit_for(text)


def with_check_digit(vin: str) -> str:
    """The same VIN with position 9 set to its correct check digit."""
    return vin[:CHECK_DIGIT_INDEX] + check_digit_for(vin) + vin[CHECK_DIGIT_INDEX + 1 :]


def same_value_characters(char: str) -> tuple[str, ...]:
    """Other alphabet characters with the same transliteration value."""
    value = TRANSLITERATION[char]
    return tuple(c for c in ALPHABET if c != char and TRANSLITERATION[c] == value)


def different_value_characters(char: str) -> tuple[str, ...]:
    """Alphabet characters with a different transliteration value."""
    value = TRANSLITERATION[char]
    return tuple(c for c in ALPHABET if TRANSLITERATION[c] != value)


def replace_at(vin: str, index: int, char: str) -> str:
    """The VIN with one position replaced."""
    return vin[:index] + char + vin[index + 1 :]
