"""Whole file validation and verbatim raw rows of an uploaded tape (SPEC 10.1, SPEC_DECISIONS 25).

The file is decoded as UTF-8 (a leading byte order mark is noted and set aside), split
into physical lines on `\\r\\n`, `\\n` or `\\r` with their terminators kept, and the lines
are fed to a strict CSV parser (`,` and `"`). After each parsed record, the lines it
consumed since the previous record are that record's verbatim text. A completely blank
line is no record and gets no row number: its text joins the next record, or the
trailer after the last one. The header, every row text and the trailer therefore
rebuild the uploaded bytes exactly, and intake refuses a file for which they do not.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from onepledge_bank_agent.domain.canonical_json import sha256_hex
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
BOM = "﻿"
BOM_BYTES = BOM.encode("utf-8")
_FIELD_LIMIT_MESSAGE = "field larger than field limit"
_PHYSICAL_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


@dataclass(frozen=True)
class TapeLayout:
    """What surrounds the data rows of a file: BOM, verbatim header record and trailer."""

    has_bom: bool
    header_text: str
    header_cells: tuple[str, ...]
    trailer_text: str


@dataclass(frozen=True)
class RawRow:
    """One data row exactly as sent: its verbatim text, its cells in order and derived forms."""

    row_number: int
    text: str
    cells: tuple[str, ...]
    payload: Mapping[str, str]
    row_sha256: str


@dataclass(frozen=True)
class ParsedTape:
    """A validated tape file."""

    file_sha256: str
    layout: TapeLayout
    header_map: HeaderMap
    rows: tuple[RawRow, ...]

    @property
    def rows_sha256(self) -> str:
        """SHA-256 of the row hashes joined by newlines, in row number order."""
        return rows_sha256(row.row_sha256 for row in self.rows)


@dataclass(frozen=True)
class SourceRecord:
    """One parsed CSV record and the source text it was parsed from."""

    text: str
    cells: tuple[str, ...]


def row_sha256(text: str) -> str:
    """SHA-256 of the UTF-8 bytes of a row's verbatim text."""
    return sha256_hex(text.encode("utf-8"))


def rows_sha256(row_hashes: Iterable[str]) -> str:
    """SHA-256 of the row hashes in row number order, joined by newlines."""
    return sha256_hex("\n".join(row_hashes).encode("utf-8"))


def raw_payload(header: Sequence[str], cells: Sequence[str]) -> Mapping[str, str]:
    """Header text to cell text; cells beyond the header become `__extra_1`, `__extra_2`, ..."""
    payload = dict(zip(header, cells, strict=False))
    for index, cell in enumerate(cells[len(header) :], start=1):
        payload[f"{EXTRA_PREFIX}{index}"] = cell
    return MappingProxyType(payload)


def reconstruct(layout: TapeLayout, row_texts: Iterable[str]) -> bytes:
    """The file bytes rebuilt from the stored forms (SPEC_DECISIONS 25 rule 1)."""
    body = layout.header_text + "".join(row_texts) + layout.trailer_text
    return (BOM_BYTES if layout.has_bom else b"") + body.encode("utf-8")


def physical_lines(text: str) -> list[str]:
    """The lines of a text with their terminators (`\\r\\n`, `\\n` or `\\r`) kept."""
    return _PHYSICAL_LINE.findall(text)


def split_records(text: str) -> tuple[list[SourceRecord], str]:
    """Every CSV record with its verbatim source text, and the trailing text after the last one."""
    consumed: list[str] = []

    def feed() -> Iterator[str]:
        for line in physical_lines(text):
            consumed.append(line)
            yield line

    records: list[SourceRecord] = []
    pending = ""
    try:
        for cells in csv.reader(feed(), delimiter=",", quotechar='"', strict=True):
            chunk = pending + "".join(consumed)
            consumed.clear()
            if cells:
                records.append(SourceRecord(chunk, tuple(cells)))
                pending = ""
            else:
                pending = chunk
    except csv.Error as error:
        if _FIELD_LIMIT_MESSAGE in str(error):
            raise TapeTooLargeError from None
        raise TapeMalformedError from None
    return records, pending + "".join(consumed)


def _decode(data: bytes) -> tuple[bool, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise TapeNotUtf8Error from None
    if "\x00" in text:
        raise TapeMalformedError
    return text.startswith(BOM), text.removeprefix(BOM)


def _check_limits(records: Sequence[SourceRecord]) -> None:
    if len(records) - 1 > MAX_DATA_ROWS:
        raise TapeTooLargeError
    for record in records:
        if len(record.cells) > MAX_COLUMNS or any(len(cell) > MAX_CELL_LENGTH for cell in record.cells):
            raise TapeTooLargeError


type RecordSplitter = Callable[[str], tuple[list[SourceRecord], str]]


class TapeFileReader:
    """Validates an uploaded file and turns it into verbatim raw rows, or raises the matching tape error.

    The record splitter is a collaborator (SPEC_DECISIONS 53.10). The byte exact reconstruction
    check stays after it as defence in depth: records that do not rebuild the uploaded bytes are
    `TAPE_MALFORMED`, whatever splitter produced them.
    """

    def __init__(self, splitter: RecordSplitter = split_records) -> None:
        self._split = splitter

    def read(self, data: bytes, kind: TapeKind) -> ParsedTape:
        """Checks in the order of the SPEC 10.1 table, then the byte exact reconstruction."""
        if len(data) > MAX_TAPE_BYTES:
            raise TapeTooLargeError
        has_bom, text = _decode(data)
        records, trailer = self._split(text)
        if len(records) < MIN_RECORDS:
            raise TapeEmptyError
        header = records[0]
        header_map = HeaderMap.from_header(header.cells, kind)
        _check_limits(records)
        layout = TapeLayout(has_bom, header.text, header.cells, trailer)
        rows = tuple(
            RawRow(
                number,
                record.text,
                record.cells,
                raw_payload(header.cells, record.cells),
                row_sha256(record.text),
            )
            for number, record in enumerate(records[1:], start=1)
        )
        if reconstruct(layout, (row.text for row in rows)) != data:
            raise TapeMalformedError
        return ParsedTape(sha256_hex(data), layout, header_map, rows)


_UNSAFE_NAME_CHARACTER = re.compile(r"[^A-Za-z0-9._-]")
_PLACEHOLDER_NAME = re.compile(r"[._]*")
MAX_FILE_NAME_LENGTH = 255
UNNAMED_FILE = "unnamed"


def sanitise_file_name(name: str) -> str:
    """Base name only, characters outside `[A-Za-z0-9._-]` as `_`, at most 255 characters (SPEC 10.1 step 4).

    After that, a name that is empty or made only of `.` and `_` characters is stored as
    `unnamed` (SPEC_DECISIONS 29), so a stored name never is a path step or a blank.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    safe = _UNSAFE_NAME_CHARACTER.sub("_", base)[:MAX_FILE_NAME_LENGTH]
    return UNNAMED_FILE if _PLACEHOLDER_NAME.fullmatch(safe) else safe
