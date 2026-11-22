"""An independent computation of which tape rows a bank should accept and which loans it should hold.

Written for the tests from SPEC 10.4, 10.5, 11 and SPEC_DECISIONS 45 only, sharing neither
code nor technique with the bank agent or datagen (SPEC_DECISIONS 50): no regular
expression and no `int`, `float`, `Decimal` or `strptime`. Every value is read by walking
its characters against literal ASCII sets and built by arithmetic. It reads the tape file
(so it knows the true column order) and the three outside records CSV files of a data set.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

_DIGIT = {"0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9}
_LOWER_TO_UPPER = dict(zip("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", strict=True))
_UPPER = frozenset(_LOWER_TO_UPPER.values())
_ALPHABET = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"
_VALUES = dict(
    zip(
        "ABCDEFGHJKLMNPRSTUVWXYZ",
        [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9],
        strict=True,
    )
)
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
_SPACE = " \t\r\n"
_BORROWER_SEPARATORS = " -_./"
_LOAN_EXTRA = "-_/"
_SUFFIXES = [["LLC"], ["L", "L", "C"], ["INC"], ["INCORPORATED"], ["CORP"], ["CORPORATION"], ["CO"],
             ["COMPANY"], ["LTD"], ["LIMITED"], ["LP"], ["L", "P"]]  # fmt: skip
_TOTALS = {"TOTAL", "TOTALS", "GRAND TOTAL", "SUBTOTAL"}


def _trim(text: str) -> str:
    start, end = 0, len(text)
    while start < end and text[start] in _SPACE:
        start += 1
    while end > start and text[end - 1] in _SPACE:
        end -= 1
    return text[start:end]


def _upper(text: str) -> str:
    return "".join(_LOWER_TO_UPPER.get(char, char) for char in text)


def _words(text: str) -> list[str]:
    words, current = [], ""
    for char in text:
        if char in _SPACE:
            if current:
                words.append(current)
            current = ""
        else:
            current += char
    return [*words, current] if current else words


def _number(text: str) -> int | None:
    """A run of ASCII digits as a number, or None."""
    if not text or any(char not in _DIGIT for char in text):
        return None
    value = 0
    for char in text:
        value = value * 10 + _DIGIT[char]
    return value


def valid_vin(text: str) -> bool:
    """ISO 3779 check digit, computed here from scratch."""
    if len(text) != 17 or any(char not in _ALPHABET for char in text):
        return False
    total = sum((_DIGIT[c] if c in _DIGIT else _VALUES[c]) * w for c, w in zip(text, _WEIGHTS, strict=True))
    expected = "X" if total % 11 == 10 else "0123456789"[total % 11]
    return text[8] == expected


def _vin(cell: str) -> str | None:
    text = "".join(char for char in _upper(cell) if char not in " -")
    if valid_vin(text):
        return text
    swapped = text.replace("O", "0").replace("Q", "0").replace("I", "1")
    return swapped if valid_vin(swapped) else None


def _dollars(text: str) -> int | None:
    """`1234` or `1,234,567` (groups of three after the first) as a whole number of dollars."""
    if "," not in text:
        return _number(text)
    groups = text.split(",")
    if not 1 <= len(groups[0]) <= 3 or any(len(group) != 3 for group in groups[1:]):
        return None
    return _number("".join(groups))


def _cents(cell: str) -> int | None:
    text = cell[1:] if cell[:1] == "$" else cell
    whole, dot, fraction = text.partition(".")
    if dot and not 1 <= len(fraction) <= 2:
        return None
    dollars = _dollars(whole)
    part = _number(fraction + "0" * (2 - len(fraction))) if dot else 0
    if dollars is None or part is None:
        return None
    return dollars * 100 + part


def _calendar(year: int | None, month: int | None, day: int | None) -> date | None:
    if year is None or month is None or day is None or not 1 <= month <= 12:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _day(cell: str) -> date | None:
    if len(cell) == 10 and cell[4] == "-" and cell[7] == "-":
        return _calendar(_number(cell[:4]), _number(cell[5:7]), _number(cell[8:]))
    if len(cell) == 10 and cell[2] == "/" and cell[5] == "/":
        return _calendar(_number(cell[6:]), _number(cell[:2]), _number(cell[3:5]))
    if len(cell) == 11 and cell[2] == "-" and cell[6] == "-" and _upper(cell[3:6]) in _MONTHS:
        return _calendar(_number(cell[7:]), _MONTHS.index(_upper(cell[3:6])) + 1, _number(cell[:2]))
    return None


def _borrower_ok(cell: str) -> bool:
    kept = ""
    for char in cell:
        if char in _BORROWER_SEPARATORS:
            continue
        if char not in _DIGIT and _upper(char) not in _UPPER:
            return False
        kept += char
    return 4 <= len(kept) <= 32


def _loan_ok(cell: str) -> bool:
    def plain(char: str) -> bool:
        return char in _DIGIT or _upper(char) in _UPPER

    return 1 <= len(cell) <= 40 and plain(cell[0]) and all(plain(c) or c in _LOAN_EXTRA for c in cell[1:])


def _unsafe(raw: str) -> bool:
    trimmed = _trim(raw)
    leading = "=+@\t\r"
    return (
        (raw[:1] in leading and raw != "")
        or (trimmed[:1] in leading and trimmed != "")
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in raw)
    )


@dataclass(frozen=True)
class Accepted:
    """An accepted row's canonical terms."""

    row_number: int
    vin: str
    origination: date
    amount_cents: int


def accepted_rows(tape: Path, as_of: date) -> list[Accepted]:
    """The rows a BACK_BOOK or PLEDGE tape keeps on a bank with no loans yet, first copy of a VIN only."""
    text = tape.read_bytes().decode("utf-8").removeprefix("﻿")
    records = [r for r in csv.reader(io.StringIO(text, newline="")) if r]
    header, found, seen = records[0], [], set()
    for number, record in enumerate(records[1:], start=1):
        if (
            len(record) != len(header)
            or not any(_trim(c) for c in record)
            or " ".join(_words(_upper(record[0]))) in _TOTALS
        ):
            continue
        cells = record[:6]
        if any(not _trim(c) or _unsafe(c) for c in cells):
            continue
        loan, vin_cell, borrower, start, original, balance = (_trim(c) for c in cells)
        vin, day = _vin(vin_cell), _day(start)
        amount, left = _cents(original), _cents(balance)
        if vin is None or day is None or amount is None or left is None:
            continue
        if not (_borrower_ok(borrower) and _loan_ok(loan)):
            continue
        if not (date(2000, 1, 1) <= day <= as_of and 10_000 <= amount <= 25_000_000 and 0 < left <= amount):
            continue
        if vin in seen:
            continue
        seen.add(vin)
        found.append(Accepted(number, vin, day, amount))
    return found


def _name(text: str) -> str:
    spaced = "".join(
        char if char in _DIGIT or char in _UPPER else " " for char in _upper(text.replace("&", " AND "))
    )
    tokens = _words(spaced)
    changed = True
    while changed:
        changed = False
        for parts in _SUFFIXES:
            if tokens[-len(parts) :] == parts and len(tokens) >= len(parts):
                tokens, changed = tokens[: -len(parts)], True
    return " ".join(tokens)


def _iso(text: str) -> date:
    day = _day(text)
    assert day is not None, "outside records dates are ISO dates"
    return day


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def held_vins(rows: list[Accepted], data_dir: Path, lender: str, as_of: date) -> set[str]:
    """The accepted VINs that fail at least one of the four local checks."""
    records = data_dir / "outside_records"
    titles = {r["vin"]: r for r in _rows(records / "titles.csv")}
    payments: dict[str, list[date]] = {}
    for r in _rows(records / "payments.csv"):
        payments.setdefault(r["vin"], []).append(_iso(r["paid_on"]))
    contracts: dict[str, list[dict[str, str]]] = {}
    for r in _rows(records / "contracts.csv"):
        contracts.setdefault(r["vin"], []).append(r)
    own = _name(lender)
    held = set()
    for row in rows:
        age = (as_of - row.origination).days
        title = titles.get(row.vin)
        title_fails = title is None and age > 30
        lien_fails = title is not None and _name(title["lienholder_name"] or "") != own
        window = [d for d in payments.get(row.vin, []) if as_of - timedelta(days=90) <= d <= as_of]
        payment_fails = age >= 45 and not window
        mine = [c for c in contracts.get(row.vin, []) if _name(c["lender_name"]) == own]
        terms_match = any(
            _number(c["amount_cents"]) == row.amount_cents
            and abs((_iso(c["signed_on"]) - row.origination).days) <= 3
            for c in mine
        )
        if title_fails or lien_fails or payment_fails or not terms_match:
            held.add(row.vin)
    return held
