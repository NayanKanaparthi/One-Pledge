"""Tape parsing, header mapping and row cleaning rules of SPEC 10.2 to 10.4 (oracle implementation).

Independent of the bank agent in technique as well as code (SPEC_DECISIONS 50): there is
no regular expression here. Every value is parsed by walking the cell, classifying each
character against literal ASCII sets, and building numbers by arithmetic over the ten
ASCII digits (SPEC_DECISIONS 45). Trimming removes ASCII space, tab, CR and LF only.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from onepledge_datagen.domain.calendar import MONTH_ABBREVIATIONS
from onepledge_datagen.domain.errors import DataGenError
from onepledge_datagen.domain.oracle.model import Action, CleanedRow, Issue, Problem, Terms
from onepledge_datagen.domain.text_values import digits_value, is_ascii_digits
from onepledge_datagen.domain.vin import ALPHABET, VIN_LENGTH, is_valid_vin

HEADER_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "lender_loan_number": ("loan #", "loan_id", "loan number", "loan_number"),
        "vin": ("vin", "vehicle_vin"),
        "borrower_ref": ("cust id", "borrower_id", "customer id", "borrower_ref"),
        "origination_date": ("orig date", "origination_date"),
        "original_amount": ("orig amt", "original_amount"),
        "current_balance": ("curr bal", "current_balance"),
    }
)
LOAN_FIELDS: tuple[str, ...] = tuple(HEADER_ALIASES)
RELEASE_FIELDS: tuple[str, ...] = ("vin",)
NON_DATA_MARKERS = frozenset({"TOTAL", "TOTALS", "GRAND TOTAL", "SUBTOTAL"})
UNSAFE_LEADING = frozenset("=+@\t\r")
EARLIEST_DATE = date(2000, 1, 1)
MIN_ORIGINAL_CENTS = 10_000
MAX_ORIGINAL_CENTS = 25_000_000
ASCII_WHITESPACE = " \t\r\n"
ASCII_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"
ASCII_LETTERS_AND_DIGITS = frozenset(ASCII_UPPER + ASCII_LOWER + "0123456789")
_CANONICAL_BORROWER = frozenset(ASCII_UPPER + "0123456789")
BORROWER_SEPARATORS = frozenset(" -_./")
BORROWER_LENGTHS = range(4, 33)
VIN_SEPARATORS = frozenset(" -")
LOAN_NUMBER_REST = ASCII_LETTERS_AND_DIGITS | frozenset("-_/")
MAX_LOAN_NUMBER_LENGTH = 40
_THOUSANDS_GROUP = 3
_MONTHS: Mapping[str, int] = MappingProxyType(
    {ascii_name.upper(): index + 1 for index, ascii_name in enumerate(MONTH_ABBREVIATIONS)}
)


def ascii_upper(text: str) -> str:
    """Upper case for the 26 ASCII letters only; every other character is left as it is."""
    return "".join(ASCII_UPPER[ASCII_LOWER.index(char)] if char in ASCII_LOWER else char for char in text)


def ascii_lower(text: str) -> str:
    """Lower case for the 26 ASCII letters only."""
    return "".join(ASCII_LOWER[ASCII_UPPER.index(char)] if char in ASCII_UPPER else char for char in text)


def ascii_trim(text: str) -> str:
    """The cell without leading and trailing ASCII space, tab, CR and LF."""
    return text.strip(ASCII_WHITESPACE)


class TapeFormatError(DataGenError):
    """A whole file cannot be read as a tape (not UTF-8, malformed CSV, or a bad header)."""

    code = "TAPE_FORMAT"


@dataclass(frozen=True)
class ParsedTape:
    """Header cells and data records, completely blank lines skipped."""

    header: tuple[str, ...]
    records: tuple[tuple[str, ...], ...]


def parse_tape(data: bytes) -> ParsedTape:
    """Decode UTF-8 (a leading BOM is stripped) and parse strict CSV."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise TapeFormatError("not UTF-8") from None
    text = text.removeprefix("﻿")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"', strict=True))
    except csv.Error:
        raise TapeFormatError("malformed CSV") from None
    records = [tuple(row) for row in rows if row]
    if not records:
        raise TapeFormatError("empty")
    return ParsedTape(header=records[0], records=tuple(records[1:]))


def map_header(header: Sequence[str], required: Sequence[str]) -> dict[str, int]:
    """Canonical field to column index, after trimming, lower casing and collapsing whitespace."""
    lookup = {alias: field for field, aliases in HEADER_ALIASES.items() for alias in aliases}
    mapping: dict[str, int] = {}
    seen: set[str] = set()
    for index, cell in enumerate(header):
        name = " ".join(_ascii_words(ascii_lower(cell)))
        if not name or name in seen:
            raise TapeFormatError("empty or duplicate header name")
        seen.add(name)
        field = lookup.get(name)
        if field is None:
            continue
        if field in mapping:
            raise TapeFormatError("two header names map to one field")
        mapping[field] = index
    if any(field not in mapping for field in required):
        raise TapeFormatError("required field missing")
    return mapping


def _ascii_words(text: str) -> list[str]:
    """The text split at runs of ASCII whitespace, empty parts dropped."""
    words, current = [], ""
    for char in text:
        if char in ASCII_WHITESPACE:
            if current:
                words.append(current)
            current = ""
        else:
            current += char
    return [*words, current] if current else words


def _unsafe(raw: str, trimmed: str) -> bool:
    """A formula leading character (raw or trimmed) or any control character."""
    if (raw and raw[0] in UNSAFE_LEADING) or (trimmed and trimmed[0] in UNSAFE_LEADING):
        return True
    return any(unicodedata.category(char) == "Cc" for char in raw)


@dataclass(frozen=True)
class _FieldResult:
    """The cleaned value of one field and the issues found on it."""

    value: str | int | None
    issues: tuple[Issue, ...]


def _rejected(field: str, problem: Problem, *fixed: Issue) -> _FieldResult:
    """A REJECTED result for one field, after the FIXED issues already found on it."""
    return _FieldResult(None, (*fixed, Issue(field, problem, Action.REJECTED)))


def clean_vin(text: str) -> _FieldResult:
    """VIN rules of SPEC 10.4 on a trimmed, non-empty cell; only ASCII letters change case."""
    fixed: list[Issue] = []
    value = text
    if any(char in ASCII_LOWER for char in value):
        value = ascii_upper(value)
        fixed.append(Issue("vin", Problem.VIN_CASE_NORMALISED, Action.FIXED))
    if any(char in VIN_SEPARATORS for char in value):
        value = "".join(char for char in value if char not in VIN_SEPARATORS)
        fixed.append(Issue("vin", Problem.VIN_SEPARATORS_REMOVED, Action.FIXED))
    if len(value) != VIN_LENGTH:
        return _rejected("vin", Problem.VIN_LENGTH_INVALID, *fixed)
    if any(char in "OQI" for char in value):
        candidate = value.replace("O", "0").replace("Q", "0").replace("I", "1")
        if is_valid_vin(candidate):
            value = candidate
            fixed.append(Issue("vin", Problem.VIN_CHARACTER_SUBSTITUTED, Action.FIXED))
    if any(char not in ALPHABET for char in value):
        return _rejected("vin", Problem.VIN_CHARACTER_INVALID, *fixed)
    if not is_valid_vin(value):
        return _rejected("vin", Problem.VIN_CHECK_DIGIT_INVALID, *fixed)
    return _FieldResult(value, tuple(fixed))


def clean_borrower(text: str) -> _FieldResult:
    """ASCII upper case and the five separators removed (SPEC_DECISIONS 45), then 4 to 32 of A-Z0-9.

    Any other character is never dropped: it stays in the value and fails the pattern.
    """
    canonical = "".join(char for char in ascii_upper(text) if char not in BORROWER_SEPARATORS)
    fixed = (
        () if canonical == text else (Issue("borrower_ref", Problem.BORROWER_REF_NORMALISED, Action.FIXED),)
    )
    if len(canonical) not in BORROWER_LENGTHS or any(char not in _CANONICAL_BORROWER for char in canonical):
        return _rejected("borrower_ref", Problem.BORROWER_REF_INVALID, *fixed)
    return _FieldResult(canonical, fixed)


def clean_loan_number(text: str) -> _FieldResult:
    """1 to 40 characters: an ASCII letter or digit, then ASCII letters, digits, `-`, `_` or `/`."""
    valid = (
        1 <= len(text) <= MAX_LOAN_NUMBER_LENGTH
        and text[0] in ASCII_LETTERS_AND_DIGITS
        and all(char in LOAN_NUMBER_REST for char in text[1:])
    )
    if not valid:
        return _rejected("lender_loan_number", Problem.LOAN_NUMBER_INVALID)
    return _FieldResult(text, ())


def _split_fixed(text: str, separator: str, widths: tuple[int, ...]) -> list[str] | None:
    """The parts of `text` split at `separator`, when there are exactly `widths` parts of those lengths."""
    parts = text.split(separator)
    if len(parts) != len(widths) or any(
        len(part) != width for part, width in zip(parts, widths, strict=True)
    ):
        return None
    return parts


def _calendar_day(year: str, month: int | None, day: str) -> date | None:
    """The real calendar day of ASCII digit year and day texts and a month number, or None."""
    if month is None or not (is_ascii_digits(year) and is_ascii_digits(day)):
        return None
    try:
        return date(digits_value(year), month, digits_value(day))
    except ValueError:
        return None


def _month_number(text: str) -> int | None:
    """The month of two ASCII digits (range checked by the calendar), or None."""
    return digits_value(text) if is_ascii_digits(text) else None


def _parse_date(text: str) -> tuple[date | None, bool]:
    """The date of an ISO, US or 14-Aug-2026 cell, and whether the third (alternative) form was used."""
    if iso := _split_fixed(text, "-", (4, 2, 2)):
        return _calendar_day(iso[0], _month_number(iso[1]), iso[2]), False
    if us := _split_fixed(text, "/", (2, 2, 4)):
        return _calendar_day(us[2], _month_number(us[0]), us[1]), False
    if named := _split_fixed(text, "-", (2, 3, 4)):
        letters_only = all(char in ASCII_UPPER or char in ASCII_LOWER for char in named[1])
        month = _MONTHS.get(ascii_upper(named[1])) if letters_only else None
        parsed = _calendar_day(named[2], month, named[0])
        return parsed, parsed is not None
    return None, False


def clean_date(text: str, as_of: date) -> _FieldResult:
    """Accepted formats, the one fixable format, and the range rules."""
    parsed, reformatted = _parse_date(text)
    if parsed is None:
        return _rejected("origination_date", Problem.DATE_UNPARSEABLE)
    fixed = (Issue("origination_date", Problem.DATE_FORMAT_NORMALISED, Action.FIXED),) if reformatted else ()
    if parsed > as_of:
        return _rejected("origination_date", Problem.DATE_IN_FUTURE, *fixed)
    if parsed < EARLIEST_DATE:
        return _rejected("origination_date", Problem.DATE_TOO_OLD, *fixed)
    return _FieldResult(parsed.isoformat(), fixed)


def _whole_dollars(text: str) -> int | None:
    """`23450` or `23,450` (commas exactly every three digits from the right), in ASCII digits."""
    if "," not in text:
        return digits_value(text) if is_ascii_digits(text) else None
    groups = text.split(",")
    head_ok = 1 <= len(groups[0]) <= _THOUSANDS_GROUP and is_ascii_digits(groups[0])
    if not head_ok or any(len(g) != _THOUSANDS_GROUP or not is_ascii_digits(g) for g in groups[1:]):
        return None
    return digits_value("".join(groups))


def _parse_amount(text: str) -> tuple[int, bool] | None:
    """Cents and whether the form was a fixable one, for an amount without its sign."""
    body = text.removeprefix("$")
    whole_text, point, fraction = body.partition(".")
    whole = _whole_dollars(whole_text)
    if whole is None or (point and (len(fraction) not in (1, 2) or not is_ascii_digits(fraction))):
        return None
    cents = digits_value(fraction) * (10 if len(fraction) == 1 else 1) if fraction else 0
    return whole * 100 + cents, len(fraction) != len("00")


def clean_amount(field: str, text: str) -> _FieldResult:
    """Amount formats of SPEC 10.4; a leading minus is out of range."""
    negative = text.startswith("-")
    parsed = _parse_amount(text[1:] if negative else text)
    if parsed is None:
        return _rejected(field, Problem.AMOUNT_UNPARSEABLE)
    cents, fixable = parsed
    fixed = (Issue(field, Problem.AMOUNT_FORMAT_NORMALISED, Action.FIXED),) if fixable else ()
    if negative:
        return _rejected(field, Problem.AMOUNT_OUT_OF_RANGE, *fixed)
    return _FieldResult(cents, fixed)


def _cents(value: str | int | None) -> int:
    """An amount field of a row with no rejection: always the cents the amount rule produced."""
    if not isinstance(value, int):
        raise TypeError
    return value


class RowCleaner:
    """Applies the row and field rules of SPEC 10.4 to one tape's records."""

    def __init__(self, header: Sequence[str], is_release: bool, as_of: date) -> None:
        self._fields = RELEASE_FIELDS if is_release else LOAN_FIELDS
        self._columns = map_header(header, self._fields)
        self._width = len(header)
        self._as_of = as_of

    def clean(self, record: Sequence[str]) -> CleanedRow:
        """Every issue of one record, and its canonical values when nothing was rejected."""
        if (
            not any(ascii_trim(cell) for cell in record)
            or ascii_upper(ascii_trim(record[0])) in NON_DATA_MARKERS
        ):
            return CleanedRow((Issue("row", Problem.NON_DATA_ROW, Action.REJECTED),))
        if len(record) != self._width:
            return CleanedRow((Issue("row", Problem.COLUMN_COUNT_MISMATCH, Action.REJECTED),))
        values: dict[str, str | int | None] = {}
        issues: list[Issue] = []
        for field in self._fields:
            result = self._clean_field(field, record[self._columns[field]])
            values[field] = result.value
            issues.extend(result.issues)
        issues.extend(self._balance_rules(values))
        ordered = tuple(sorted(issues, key=Issue.sort_key))
        if any(issue.action == Action.REJECTED for issue in ordered):
            return CleanedRow(ordered)
        vin = str(values["vin"])
        if len(self._fields) == 1:
            return CleanedRow(ordered, vin=vin)
        terms = Terms(
            vin=vin,
            borrower_ref=str(values["borrower_ref"]),
            origination_date=str(values["origination_date"]),
            original_amount_cents=_cents(values["original_amount"]),
            lender_loan_number=str(values["lender_loan_number"]),
            current_balance_cents=_cents(values["current_balance"]),
        )
        return CleanedRow(ordered, vin=vin, terms=terms)

    def _clean_field(self, field: str, raw: str) -> _FieldResult:
        """Trim, then empty, then unsafe, then the field's own rule; the first rejection ends the field."""
        trimmed = ascii_trim(raw)
        trim_issue = (Issue(field, Problem.WHITESPACE_TRIMMED, Action.FIXED),) if trimmed != raw else ()
        if not trimmed:
            return _FieldResult(
                None, (*trim_issue, Issue(field, Problem.MISSING_REQUIRED_FIELD, Action.REJECTED))
            )
        if _unsafe(raw, trimmed):
            return _FieldResult(None, (*trim_issue, Issue(field, Problem.UNSAFE_CHARACTERS, Action.REJECTED)))
        result = self._field_rule(field, trimmed)
        return _FieldResult(result.value, (*trim_issue, *result.issues))

    def _field_rule(self, field: str, text: str) -> _FieldResult:
        """The SPEC 10.4 rule of one field on its trimmed, safe, non-empty text."""
        if field == "vin":
            return clean_vin(text)
        if field == "borrower_ref":
            return clean_borrower(text)
        if field == "lender_loan_number":
            return clean_loan_number(text)
        if field == "origination_date":
            return clean_date(text, self._as_of)
        return clean_amount(field, text)

    def _balance_rules(self, values: Mapping[str, str | int | None]) -> list[Issue]:
        """Original amount range, zero balance and balance above the original (SPEC 10.4)."""
        if "original_amount" not in self._fields:
            return []
        issues: list[Issue] = []
        original, balance = values.get("original_amount"), values.get("current_balance")
        if isinstance(original, int) and not MIN_ORIGINAL_CENTS <= original <= MAX_ORIGINAL_CENTS:
            issues.append(Issue("original_amount", Problem.AMOUNT_OUT_OF_RANGE, Action.REJECTED))
        if isinstance(balance, int) and balance == 0:
            issues.append(Issue("current_balance", Problem.ZERO_BALANCE, Action.REJECTED))
        if isinstance(original, int) and isinstance(balance, int) and balance > original:
            issues.append(Issue("current_balance", Problem.BALANCE_EXCEEDS_ORIGINAL, Action.REJECTED))
        return issues
