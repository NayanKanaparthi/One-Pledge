"""Integers and dates read from text: the one place outside records turns text into either.

SPEC_DECISIONS 37 and 45: a well formed integer is `^-?[0-9]{1,18}$` in ASCII digits;
anything else (a `+`, a space, a point, an exponent, a non-ASCII digit, an empty value,
more than 18 digits) is INVALID_INTEGER, and a well formed integer outside its bounds is
OUT_OF_RANGE. A date is `YYYY-MM-DD` in ASCII digits and a real calendar day. Values are
built by arithmetic over the ten ASCII digits, so no other character can count as one.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

MAX_DIGITS = 18
MAX_INTEGER = 10**MAX_DIGITS - 1
ASCII_DIGITS = "0123456789"
_DATE_LENGTH = len("YYYY-MM-DD")
_DATE_DASHES = (4, 7)


class IntegerIssue(StrEnum):
    """Why a text is not an acceptable integer (the issue codes of SPEC_DECISIONS 22.7)."""

    INVALID_INTEGER = "INVALID_INTEGER"
    OUT_OF_RANGE = "OUT_OF_RANGE"


def _digits_value(digits: str) -> int:
    number = 0
    for char in digits:
        number = number * 10 + ASCII_DIGITS.index(char)
    return number


def _all_digits(text: str) -> bool:
    return all(char in ASCII_DIGITS for char in text)


def parse_integer_text(text: str, minimum: int, maximum: int) -> int | IntegerIssue:
    """The integer the text writes, or the issue that refuses it."""
    negative = text.startswith("-")
    digits = text[1:] if negative else text
    if not 1 <= len(digits) <= MAX_DIGITS or not _all_digits(digits):
        return IntegerIssue.INVALID_INTEGER
    number = -_digits_value(digits) if negative else _digits_value(digits)
    if not minimum <= number <= maximum:
        return IntegerIssue.OUT_OF_RANGE
    return number


def parse_iso_date(text: str) -> date | None:
    """The calendar day `YYYY-MM-DD` writes in ASCII digits, or None."""
    if len(text) != _DATE_LENGTH or any(text[i] != "-" for i in _DATE_DASHES):
        return None
    year, month, day = text[0:4], text[5:7], text[8:10]
    if not (_all_digits(year) and _all_digits(month) and _all_digits(day)):
        return None
    try:
        return date(_digits_value(year), _digits_value(month), _digits_value(day))
    except ValueError:
        return None
