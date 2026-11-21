"""An independent computation of which tape rows a bank should accept and which loans it should hold.

Written for the tests from SPEC 10.4, 10.5 and 11 only, sharing no code with the bank agent
or datagen. It reads the tape file (so it knows the true column order) and the three
outside records CSV files of a data set.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

_ALPHABET = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"
_VALUES = dict(
    zip(
        "ABCDEFGHJKLMNPRSTUVWXYZ",
        [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9],
        strict=True,
    )
)
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
_AMOUNT = re.compile(r"^\$?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?$")
_SUFFIXES = [
    "LLC",
    "L L C",
    "INC",
    "INCORPORATED",
    "CORP",
    "CORPORATION",
    "CO",
    "COMPANY",
    "LTD",
    "LIMITED",
    "LP",
    "L P",
]
_TOTALS = {"TOTAL", "TOTALS", "GRAND TOTAL", "SUBTOTAL"}


def valid_vin(text: str) -> bool:
    """ISO 3779 check digit, computed here from scratch."""
    if len(text) != 17 or any(char not in _ALPHABET for char in text):
        return False
    total = sum((int(c) if c.isdigit() else _VALUES[c]) * w for c, w in zip(text, _WEIGHTS, strict=True))
    expected = "X" if total % 11 == 10 else str(total % 11)
    return text[8] == expected


def _vin(cell: str) -> str | None:
    text = cell.upper().replace(" ", "").replace("-", "")
    if valid_vin(text):
        return text
    swapped = text.replace("O", "0").replace("Q", "0").replace("I", "1")
    return swapped if valid_vin(swapped) else None


def _cents(cell: str) -> int | None:
    match = _AMOUNT.fullmatch(cell)
    if match is None:
        return None
    return int(match[1].replace(",", "")) * 100 + int((match[2] or "0").ljust(2, "0"))


def _day(cell: str) -> date | None:
    for pattern, shape in (
        ("%Y-%m-%d", r"\d{4}-\d\d-\d\d"),
        ("%m/%d/%Y", r"\d\d/\d\d/\d{4}"),
        ("%d-%b-%Y", r"\d\d-[A-Za-z]{3}-\d{4}"),
    ):
        if re.fullmatch(shape, cell):
            try:
                return datetime.strptime(cell, pattern).date()
            except ValueError:
                return None
    return None


def _unsafe(raw: str) -> bool:
    stripped = raw.strip()
    leading = "=+@\t\r"
    return (
        (raw[:1] in leading and raw != "")
        or (stripped[:1] in leading and stripped != "")
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
            or not any(c.strip() for c in record)
            or record[0].strip().upper() in _TOTALS
        ):
            continue
        cells = record[:6]
        if any(not c.strip() or _unsafe(c) for c in cells):
            continue
        loan, vin_cell, borrower, start, original, balance = (c.strip() for c in cells)
        vin, day = _vin(vin_cell), _day(start)
        amount, left = _cents(original), _cents(balance)
        borrower_ok = re.fullmatch(r"[A-Z0-9]{4,32}", re.sub(r"[^A-Z0-9]", "", borrower.upper())) is not None
        loan_ok = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-_/]{0,39}", loan) is not None
        if vin is None or day is None or amount is None or left is None or not (borrower_ok and loan_ok):
            continue
        if not (date(2000, 1, 1) <= day <= as_of and 10_000 <= amount <= 25_000_000 and 0 < left <= amount):
            continue
        if vin in seen:
            continue
        seen.add(vin)
        found.append(Accepted(number, vin, day, amount))
    return found


def _name(text: str) -> str:
    tokens = re.sub(r"[^A-Z0-9 ]", " ", text.upper().replace("&", " AND ")).split()
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            parts = suffix.split()
            if tokens[-len(parts) :] == parts and len(tokens) >= len(parts):
                tokens, changed = tokens[: -len(parts)], True
    return " ".join(tokens)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def held_vins(rows: list[Accepted], data_dir: Path, lender: str, as_of: date) -> set[str]:
    """The accepted VINs that fail at least one of the four local checks."""
    records = data_dir / "outside_records"
    titles = {r["vin"]: r for r in _rows(records / "titles.csv")}
    payments: dict[str, list[date]] = {}
    for r in _rows(records / "payments.csv"):
        payments.setdefault(r["vin"], []).append(date.fromisoformat(r["paid_on"]))
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
            int(c["amount_cents"]) == row.amount_cents
            and abs((date.fromisoformat(c["signed_on"]) - row.origination).days) <= 3
            for c in mine
        )
        if title_fails or lien_fails or payment_fails or not terms_match:
            held.add(row.vin)
    return held
