"""Whole file validation and raw rows of an uploaded tape (SPEC 10.1 step 2, 10.2).

Reads bytes exactly as received: UTF-8 (a leading BOM is stripped), strict CSV with
`,` and `"`. A data row is a CSV record after the header; completely blank lines are
skipped and not numbered. Each raw row maps the header text, exactly as it appears,
to the cell text, exactly as it appears.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from onepledge_bank_agent.domain.canonical_json import canonical_sha256, sha256_hex
from onepledge_bank_agent.domain.cleaning.header import EXTRA_PREFIX, HeaderMap
from onepledge_bank_agent.domain.enums import TapeKind
from onepledge_bank_agent.domain.errors import (
    TapeEmptyError,
    TapeMalformedError,
    TapeNotUtf8Error,
    TapeTooLargeError,
)

MAX_TAPE_BYTES = 5 * 1024 * 1024
MAX_DATA_ROWS = 20_000
MAX_COLUMNS = 64
MAX_CELL_LENGTH = 512
MIN_RECORDS = 2  # a header and at least one data row
_BOM = "﻿"
_FIELD_LIMIT_MESSAGE = "field larger than field limit"


@dataclass(frozen=True)
class RawRow:
    """One data row as sent: its number, its payload and the hash of the payload's canonical JSON."""

    row_number: int
    payload: Mapping[str, str]
    row_sha256: str


@dataclass(frozen=True)
class ParsedTape:
    """A validated tape file."""

    file_sha256: str
    header: tuple[str, ...]
    header_map: HeaderMap
    rows: tuple[RawRow, ...]

    @property
    def rows_sha256(self) -> str:
        """SHA-256 of the row hashes joined by newlines, in row number order (SPEC 10.2)."""
        return rows_sha256(row.row_sha256 for row in self.rows)


def row_sha256(payload: Mapping[str, str]) -> str:
    """SHA-256 of the canonical JSON of a raw payload."""
    return canonical_sha256(dict(payload))


def rows_sha256(row_hashes: Iterable[str]) -> str:
    """SHA-256 of the row hashes in row number order, joined by newlines."""
    return sha256_hex("\n".join(row_hashes).encode("utf-8"))


def raw_payload(header: Sequence[str], record: Sequence[str]) -> Mapping[str, str]:
    """Header text to cell text; cells beyond the header become `__extra_1`, `__extra_2`, ..."""
    payload = dict(zip(header, record, strict=False))
    for index, cell in enumerate(record[len(header) :], start=1):
        payload[f"{EXTRA_PREFIX}{index}"] = cell
    return MappingProxyType(payload)


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise TapeNotUtf8Error from None
    return text.removeprefix(_BOM)


def _records(text: str) -> list[list[str]]:
    if "\x00" in text:
        raise TapeMalformedError
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"', strict=True))
    except csv.Error as error:
        if _FIELD_LIMIT_MESSAGE in str(error):
            raise TapeTooLargeError from None
        raise TapeMalformedError from None
    return [row for row in rows if row]


def _check_limits(records: Sequence[Sequence[str]]) -> None:
    if len(records) - 1 > MAX_DATA_ROWS:
        raise TapeTooLargeError
    for record in records:
        if len(record) > MAX_COLUMNS or any(len(cell) > MAX_CELL_LENGTH for cell in record):
            raise TapeTooLargeError


class TapeFileReader:
    """Validates an uploaded file and turns it into raw rows, or raises the matching tape error."""

    def read(self, data: bytes, kind: TapeKind) -> ParsedTape:
        """Checks in the order of the SPEC 10.1 table."""
        if len(data) > MAX_TAPE_BYTES:
            raise TapeTooLargeError
        records = _records(_decode(data))
        if len(records) < MIN_RECORDS:
            raise TapeEmptyError
        header = tuple(records[0])
        header_map = HeaderMap.from_header(header, kind)
        _check_limits(records)
        rows = []
        for number, record in enumerate(records[1:], start=1):
            payload = raw_payload(header, record)
            rows.append(RawRow(number, payload, row_sha256(payload)))
        return ParsedTape(sha256_hex(data), header, header_map, tuple(rows))


_UNSAFE_NAME_CHARACTER = re.compile(r"[^A-Za-z0-9._-]")
MAX_FILE_NAME_LENGTH = 255
UNNAMED_FILE = "unnamed"


def sanitise_file_name(name: str) -> str:
    """Base name only, characters outside `[A-Za-z0-9._-]` as `_`, at most 255 characters (SPEC 10.1 step 4).

    A name made only of dots (`.`, `..`) or nothing at all is stored as `unnamed`, so a
    stored name never contains a path step.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    safe = _UNSAFE_NAME_CHARACTER.sub("_", base)[:MAX_FILE_NAME_LENGTH]
    return safe if safe.strip(".") else UNNAMED_FILE
