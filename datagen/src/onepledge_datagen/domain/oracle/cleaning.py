"""Tape parsing, header mapping and row cleaning rules of SPEC 10.2 to 10.4 (oracle implementation)."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from onepledge_datagen.domain.calendar import MONTH_ABBREVIATIONS
from onepledge_datagen.domain.oracle.model import Action, CleanedRow, Issue, Problem, Terms
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
_BORROWER = re.compile(r"^[A-Z0-9]{4,32}$")
_LOAN_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_/]{0,39}$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_US = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_DAY_MONTH_YEAR = re.compile(r"^(\d{2})-([A-Za-z]{3})-(\d{4})$")
_AMOUNT_EXACT = re.compile(r"^\$?(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})$")
_AMOUNT_FIXABLE = re.compile(r"^\$?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d))?$")
_MONTHS: Mapping[str, int] = MappingProxyType(
    {name.upper(): index + 1 for index, name in enumerate(MONTH_ABBREVIATIONS)}
)


class TapeFormatError(Exception):
    """A whole file cannot be read as a tape (not UTF-8, malformed CSV, or a bad header)."""


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
        name = " ".join(cell.strip().lower().split())
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


def _unsafe(raw: str, trimmed: str) -> bool:
    if (raw and raw[0] in UNSAFE_LEADING) or (trimmed and trimmed[0] in UNSAFE_LEADING):
        return True
    return any(unicodedata.category(char) == "Cc" for char in raw)


@dataclass(frozen=True)
class _FieldResult:
    value: str | int | None
    issues: tuple[Issue, ...]


def _rejected(field: str, problem: Problem, *fixed: Issue) -> _FieldResult:
    return _FieldResult(None, (*fixed, Issue(field, problem, Action.REJECTED)))


def clean_vin(text: str) -> _FieldResult:
    """VIN rules of SPEC 10.4 on a trimmed, non-empty cell."""
    fixed: list[Issue] = []
    value = text
    if any(char.islower() for char in value):
        value = value.upper()
        fixed.append(Issue("vin", Problem.VIN_CASE_NORMALISED, Action.FIXED))
    if " " in value or "-" in value:
        value = value.replace(" ", "").replace("-", "")
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
    """Upper case, characters outside A-Z0-9 removed, then the pattern."""
    canonical = re.sub(r"[^A-Z0-9]", "", text.upper())
    fixed = (
        () if canonical == text else (Issue("borrower_ref", Problem.BORROWER_REF_NORMALISED, Action.FIXED),)
    )
    if not _BORROWER.fullmatch(canonical):
        return _rejected("borrower_ref", Problem.BORROWER_REF_INVALID, *fixed)
    return _FieldResult(canonical, fixed)


def clean_loan_number(text: str) -> _FieldResult:
    """The loan number pattern."""
    if not _LOAN_NUMBER.fullmatch(text):
        return _rejected("lender_loan_number", Problem.LOAN_NUMBER_INVALID)
    return _FieldResult(text, ())


def _parse_date(text: str) -> tuple[date | None, bool]:
    try:
        if match := _ISO.fullmatch(text):
            return date(int(match[1]), int(match[2]), int(match[3])), False
        if match := _US.fullmatch(text):
            return date(int(match[3]), int(match[1]), int(match[2])), False
        if (match := _DAY_MONTH_YEAR.fullmatch(text)) and match[2].upper() in _MONTHS:
            return date(int(match[3]), _MONTHS[match[2].upper()], int(match[1])), True
    except ValueError:
        return None, False
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


def clean_amount(field: str, text: str) -> _FieldResult:
    """Amount formats of SPEC 10.4; a leading minus is out of range."""
    negative = text.startswith("-")
    body = text[1:] if negative else text
    fixed: tuple[Issue, ...] = ()
    if match := _AMOUNT_EXACT.fullmatch(body):
        cents = int(match[1].replace(",", "")) * 100 + int(match[2])
    elif match := _AMOUNT_FIXABLE.fullmatch(body):
        cents = int(match[1].replace(",", "")) * 100 + int(match[2] or 0) * 10
        fixed = (Issue(field, Problem.AMOUNT_FORMAT_NORMALISED, Action.FIXED),)
    else:
        return _rejected(field, Problem.AMOUNT_UNPARSEABLE)
    if negative:
        return _rejected(field, Problem.AMOUNT_OUT_OF_RANGE, *fixed)
    return _FieldResult(cents, fixed)


class RowCleaner:
    """Applies the row and field rules of SPEC 10.4 to one tape's records."""

    def __init__(self, header: Sequence[str], is_release: bool, as_of: date) -> None:
        self._fields = RELEASE_FIELDS if is_release else LOAN_FIELDS
        self._columns = map_header(header, self._fields)
        self._width = len(header)
        self._as_of = as_of

    def clean(self, record: Sequence[str]) -> CleanedRow:
        """Every issue of one record, and its canonical values when nothing was rejected."""
        if not any(cell.strip() for cell in record) or record[0].strip().upper() in NON_DATA_MARKERS:
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
            original_amount_cents=int(values["original_amount"] or 0),
            lender_loan_number=str(values["lender_loan_number"]),
            current_balance_cents=int(values["current_balance"] or 0),
        )
        return CleanedRow(ordered, vin=vin, terms=terms)

    def _clean_field(self, field: str, raw: str) -> _FieldResult:
        trimmed = raw.strip()
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
