"""The vehicle identification number and its ISO 3779 check digit (SPEC 5.5, 8.1).

This is the bank agent's own implementation. datagen has a separate one, so each
checks the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Self

from onepledge_bank_agent.domain.errors import InvalidVinError

VIN_LENGTH = 17
CHECK_DIGIT_POSITION = 8  # zero based index of position 9
VIN_ALPHABET = frozenset("0123456789ABCDEFGHJKLMNPRSTUVWXYZ")
_LETTER_VALUES = MappingProxyType(
    {
        **dict(zip("ABCDEFGH", range(1, 9), strict=True)),
        **dict(zip("JKLMN", range(1, 6), strict=True)),
        "P": 7,
        "R": 9,
        **dict(zip("STUVWXYZ", range(2, 10), strict=True)),
    }
)
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_MODULUS = 11
_TEN_AS_CHECK = "X"


class VinDefect(StrEnum):
    """Why a string is not a VIN."""

    LENGTH = "LENGTH"
    CHARACTER = "CHARACTER"
    CHECK_DIGIT = "CHECK_DIGIT"


def transliterate(character: str) -> int:
    """The numeric value of one VIN character (digits are their own value)."""
    if character.isdigit():
        return int(character)
    return _LETTER_VALUES[character]


def check_digit(text: str) -> str:
    """The check digit a 17 character VIN alphabet string must carry at position 9."""
    total = sum(transliterate(char) * weight for char, weight in zip(text, _WEIGHTS, strict=True))
    remainder = total % _MODULUS
    return _TEN_AS_CHECK if remainder == _MODULUS - 1 else str(remainder)


def vin_defect(text: str) -> VinDefect | None:
    """The first defect of a candidate VIN, or None when it is a valid VIN."""
    if len(text) != VIN_LENGTH:
        return VinDefect.LENGTH
    if any(char not in VIN_ALPHABET for char in text):
        return VinDefect.CHARACTER
    if text[CHECK_DIGIT_POSITION] != check_digit(text):
        return VinDefect.CHECK_DIGIT
    return None


@dataclass(frozen=True, repr=False)
class Vin:
    """A valid VIN: 17 upper case characters of the VIN alphabet with a correct check digit.

    The raw text is available only through `.value`; `repr` and `str` are masked so a
    VIN never reaches a log line or an error message by accident.
    """

    value: str

    def __post_init__(self) -> None:
        """Refuse anything that is not a valid VIN."""
        defect = vin_defect(self.value)
        if defect is not None:
            raise InvalidVinError(defect.value)

    @classmethod
    def parse(cls, text: str) -> Self:
        """Parse canonical VIN text; raises `InvalidVinError` with reason LENGTH, CHARACTER or CHECK_DIGIT."""
        return cls(text)

    def __repr__(self) -> str:
        """Masked."""
        return "Vin(***)"

    def __str__(self) -> str:
        """Masked."""
        return "Vin(***)"
