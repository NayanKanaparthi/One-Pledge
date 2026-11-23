"""Reads `DIR/outside_records/{titles,payments,contracts}.csv` as written by the data generator."""

from __future__ import annotations

import csv
import io
import os
import stat
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


# A seed CSV is bounded at 256 MiB (SPEC_DECISIONS 110).
MAX_CSV_BYTES = 256 * 1024 * 1024
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _refused(relative: str, problem: str) -> OperatorError:
    return OperatorError(f"{relative} {problem}.", "INVALID_INPUT")


def _examine(directory: Path, parts: tuple[str, ...]) -> None:
    """Refuse, by name and before anything is opened, a link anywhere on the way from `directory`
    to the file, a step that is not a real directory, and a file that is not a regular file."""
    root = Path(os.path.realpath(directory))
    step = directory
    for index, part in enumerate(parts):
        step = step / part
        relative = "/".join(parts[: index + 1])
        try:
            mode = os.lstat(step).st_mode
        except FileNotFoundError:
            return  # Opening it names it as unreadable.
        last = index == len(parts) - 1
        if stat.S_ISLNK(mode):
            inside = Path(os.path.realpath(step)).is_relative_to(root)
            raise _refused(relative, "is not a regular file" if inside else "is a link out of the directory")
        if not last and not stat.S_ISDIR(mode):
            raise _refused(relative, "is not a directory")
        if last and stat.S_ISDIR(mode):
            raise _refused(relative, "is a directory where a file is expected")
        if last and not stat.S_ISREG(mode):
            raise _refused(relative, "is not a regular file")


def _read(directory: Path, name: str) -> bytes:
    relative = f"outside_records/{name}"
    _examine(directory, ("outside_records", name))
    with os.fdopen(os.open(directory / relative, _OPEN_FLAGS), "rb") as handle:
        status = os.fstat(handle.fileno())
        # The file may have been replaced between the examination and the open.
        if not stat.S_ISREG(status.st_mode):
            raise _refused(relative, "is not a regular file")
        if status.st_size > MAX_CSV_BYTES:
            raise _refused(relative, "is larger than 256 MiB")
        data = handle.read(MAX_CSV_BYTES + 1)
    if len(data) > MAX_CSV_BYTES:
        raise _refused(relative, "is larger than 256 MiB")
    return data


def _rows(directory: Path, name: str) -> list[list[str]]:
    path = directory / "outside_records" / name
    try:
        text = _read(directory, name).decode("utf-8")
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
