"""ASCII text rules shared by every parser of the bank agent (SPEC_DECISIONS 37 and 45).

Wherever SPEC writes a digit it means one of the ten ASCII characters `0` to `9`; letters
in formats are ASCII letters; trimming removes ASCII space, tab, carriage return and line
feed only. Python's own `str.isdigit`, `int(str)`, `Decimal(str)`, `str.strip()`,
`str.upper()` and `\\d` all accept or map other Unicode characters, so no parser uses them
on input text: they use these functions. `parse_integer` is the one function of this
package that turns text into an integer.
"""

from __future__ import annotations

from typing import Final

from onepledge_bank_agent.domain.errors import InvalidValueError

DIGITS: Final = frozenset("0123456789")
UPPER: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
LOWER: Final = frozenset("abcdefghijklmnopqrstuvwxyz")
ASCII_WHITESPACE: Final = " \t\r\n"
MAX_INTEGER_DIGITS: Final = 18
INVALID_INTEGER: Final = "INVALID_INTEGER"
OUT_OF_RANGE: Final = "OUT_OF_RANGE"
_TO_UPPER: Final = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_TO_LOWER: Final = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def is_digits(text: str) -> bool:
    """True for one or more ASCII digits and nothing else."""
    return bool(text) and all(char in DIGITS for char in text)


def is_ascii_alnum(char: str) -> bool:
    """One ASCII letter or digit."""
    return char in DIGITS or char in UPPER or char in LOWER


def trim(text: str) -> str:
    """The text without leading and trailing ASCII space, tab, carriage return and line feed."""
    return text.strip(ASCII_WHITESPACE)


def ascii_upper(text: str) -> str:
    """ASCII letters upper cased; every other character unchanged (the long s U+017F never becomes S)."""
    return text.translate(_TO_UPPER)


def ascii_lower(text: str) -> str:
    """ASCII letters lower cased; every other character unchanged."""
    return text.translate(_TO_LOWER)


def split_ascii_whitespace(text: str) -> list[str]:
    """The words of a text separated by runs of ASCII whitespace."""
    words: list[str] = []
    current: list[str] = []
    for char in text:
        if char in ASCII_WHITESPACE:
            if current:
                words.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        words.append("".join(current))
    return words


def parse_integer(text: str, field: str, low: int | None = None, high: int | None = None) -> int:
    """`^-?[0-9]{1,18}$` in ASCII digits, else INVALID_INTEGER; outside `[low, high]`, OUT_OF_RANGE.

    The value is built by arithmetic from the digits, never by `int(str)`.
    """
    negative = text.startswith("-")
    digits = text[1:] if negative else text
    if not is_digits(digits) or len(digits) > MAX_INTEGER_DIGITS:
        raise InvalidValueError(field, INVALID_INTEGER)
    value = 0
    for char in digits:
        value = value * 10 + (ord(char) - ord("0"))
    value = -value if negative else value
    if (low is not None and value < low) or (high is not None and value > high):
        raise InvalidValueError(field, OUT_OF_RANGE)
    return value
