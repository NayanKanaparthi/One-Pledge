"""Reads `DIR/outside_records/{titles,payments,contracts}.csv` as written by the data generator."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from types import MappingProxyType

from onepledge_outside_records.application.ports import RecordSet
from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.domain.records import VIN_PATTERN, Contract, Payment, TitleRecord
from onepledge_outside_records.domain.text_values import (
    MAX_INTEGER,
    IntegerIssue,
    parse_integer_text,
    parse_iso_date,
)

HEADERS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "titles.csv": ("vin", "state", "title_status", "lienholder_name", "lien_recorded_on"),
        "payments.csv": ("payment_id", "vin", "paid_on", "amount_cents", "deposit_bank_code"),
        "contracts.csv": (
            "contract_id",
            "vin",
            "lender_name",
            "amount_cents",
            "signed_on",
            "document_sha256",
        ),
    }
)


def _rows(directory: Path, name: str) -> list[list[str]]:
    path = directory / "outside_records" / name
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise OperatorError(
            f"{path} cannot be read; pass the directory written by onepledge-datagen.", "INVALID_INPUT"
        ) from None
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error:
        raise OperatorError(f"{path} is not well formed CSV.", "INVALID_INPUT") from None
    if not rows or tuple(rows[0]) != HEADERS[name]:
        raise OperatorError(
            f"{path} does not start with the header {', '.join(HEADERS[name])}.", "INVALID_INPUT"
        )
    for line, row in enumerate(rows[1:], start=2):
        if len(row) != len(HEADERS[name]):
            raise OperatorError(
                f"{path} line {line} has {len(row)} cells instead of {len(HEADERS[name])}.", "INVALID_INPUT"
            )
    return rows[1:]


class _Cells:
    """The cells of one CSV row, each parsed with an error naming the file, line and column."""

    def __init__(self, name: str, line: int, row: list[str]) -> None:
        self._name, self._line, self._row = name, line, row

    def _refuse(self, column: int, problem: str) -> OperatorError:
        column_name = HEADERS[self._name][column]
        return OperatorError(
            f"{self._name} line {self._line} column {column_name} {problem}.", "INVALID_INPUT"
        )

    def text(self, column: int) -> str:
        return self._row[column]

    def vin(self, column: int) -> str:
        value = self._row[column]
        if not VIN_PATTERN.fullmatch(value):
            raise self._refuse(column, "does not match the VIN pattern of the contract")
        return value

    def day(self, column: int) -> date:
        value = parse_iso_date(self._row[column])
        if value is None:
            raise self._refuse(column, "is not a YYYY-MM-DD date")
        return value

    def optional_day(self, column: int) -> date | None:
        return self.day(column) if self._row[column] else None

    def cents(self, column: int) -> int:
        value = parse_integer_text(self._row[column], 1, MAX_INTEGER)
        if isinstance(value, IntegerIssue):
            raise self._refuse(column, "is not a positive whole number of cents")
        return value


def _cells(directory: Path, name: str) -> list[_Cells]:
    return [_Cells(name, line, row) for line, row in enumerate(_rows(directory, name), start=2)]


class CsvRecordsSource:
    """The three CSV files of a data directory."""

    def load(self, data_dir: Path) -> RecordSet:
        """Parse and validate every row; a bad cell stops the load naming its file, line and column."""
        titles = tuple(
            TitleRecord(c.vin(0), c.text(1), c.text(2), c.text(3) or None, c.optional_day(4))
            for c in _cells(data_dir, "titles.csv")
        )
        payments = tuple(
            Payment(c.text(0), c.vin(1), c.day(2), c.cents(3), c.text(4))
            for c in _cells(data_dir, "payments.csv")
        )
        contracts = tuple(
            Contract(c.text(0), c.vin(1), c.text(2), c.cents(3), c.day(4), c.text(5))
            for c in _cells(data_dir, "contracts.csv")
        )
        return RecordSet(titles, payments, contracts)
