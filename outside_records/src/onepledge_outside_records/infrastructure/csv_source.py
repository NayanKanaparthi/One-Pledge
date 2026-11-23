"""Reads `DIR/outside_records/{titles,payments,contracts}.csv` as written by the data generator."""

from __future__ import annotations

import csv
import io
import os
import stat
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from types import MappingProxyType

from onepledge_outside_records.application.ports import RecordSet
from onepledge_outside_records.domain.errors import OperatorError, RecordsRefusedError
from onepledge_outside_records.domain.record_rules import record_problems
from onepledge_outside_records.domain.records import Contract, Payment, TitleRecord
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
    return rows[1:]


# The key that must not repeat inside each file, and the contract schema of its rows.
_KEYS: Mapping[str, str] = MappingProxyType(
    {"titles.csv": "vin", "payments.csv": "payment_id", "contracts.csv": "contract_id"}
)
_RECORDS: Mapping[str, str] = MappingProxyType(
    {"titles.csv": "TitleRecord", "payments.csv": "Payment", "contracts.csv": "Contract"}
)
_OPTIONAL = frozenset({"lienholder_name", "lien_recorded_on"})


def _document(name: str, row: list[str]) -> dict[str, object]:
    """The row as the API would return it: an empty optional cell is null, an amount a number."""
    document: dict[str, object] = dict(zip(HEADERS[name], row, strict=True))
    for column in _OPTIONAL & set(document):
        if document[column] == "":
            document[column] = None
    if "amount_cents" in document:
        amount = parse_integer_text(str(document["amount_cents"]), 1, MAX_INTEGER)
        document["amount_cents"] = None if isinstance(amount, IntegerIssue) else amount
    return document


def _day(document: Mapping[str, object], column: str) -> date | None:
    value = document[column]
    return parse_iso_date(value) if isinstance(value, str) else None


def _cents(document: Mapping[str, object]) -> int | None:
    value = document["amount_cents"]
    return value if isinstance(value, int) else None


def _title(d: Mapping[str, object]) -> TitleRecord:
    name = d["lienholder_name"]
    return TitleRecord(
        str(d["vin"]),
        str(d["state"]),
        str(d["title_status"]),
        name if isinstance(name, str) else None,
        _day(d, "lien_recorded_on"),
    )


def _payment(d: Mapping[str, object]) -> Payment | None:
    paid_on, cents = _day(d, "paid_on"), _cents(d)
    if paid_on is None or cents is None:
        return None
    return Payment(str(d["payment_id"]), str(d["vin"]), paid_on, cents, str(d["deposit_bank_code"]))


def _contract(d: Mapping[str, object]) -> Contract | None:
    signed_on, cents = _day(d, "signed_on"), _cents(d)
    if signed_on is None or cents is None:
        return None
    return Contract(
        str(d["contract_id"]),
        str(d["vin"]),
        str(d["lender_name"]),
        cents,
        signed_on,
        str(d["document_sha256"]),
    )


def _checked[R](
    directory: Path, name: str, build: Callable[[Mapping[str, object]], R | None], problems: list[str]
) -> tuple[R, ...]:
    """The records of one file; every problem of every row is added to `problems`.

    A row is built into its record only when the record rules found nothing wrong with it,
    so a value the rules accepted is the value stored.
    """
    records: list[R] = []
    first_line: dict[object, int] = {}
    key = _KEYS[name]
    for line, row in enumerate(_rows(directory, name), start=2):
        if len(row) != len(HEADERS[name]):
            problems.append(f"{name} line {line}: has {len(row)} cells instead of {len(HEADERS[name])}")
            continue
        document = _document(name, row)
        found = [
            f"{name} line {line}, column {field}: {problem}"
            for field, problem in record_problems(_RECORDS[name], document)
        ]
        earlier = first_line.setdefault(document[key], line)
        if earlier != line:
            found.append(f"{name} line {line}, column {key}: repeats the {key} of line {earlier}")
        record = None if found else build(document)
        if record is not None:
            records.append(record)
        problems.extend(found)
    return tuple(records)


class CsvRecordsSource:
    """The three CSV files of a data directory."""

    def load(self, data_dir: Path) -> RecordSet:
        """Every row of the three files, checked against the contract before anything is returned.

        A problem anywhere refuses the whole data set with every problem named
        (SPEC_DECISIONS 109): nothing is written unless everything is valid.
        """
        problems: list[str] = []
        titles = _checked(data_dir, "titles.csv", _title, problems)
        payments = _checked(data_dir, "payments.csv", _payment, problems)
        contracts = _checked(data_dir, "contracts.csv", _contract, problems)
        if problems:
            raise RecordsRefusedError(tuple(problems))
        return RecordSet(titles, payments, contracts)
