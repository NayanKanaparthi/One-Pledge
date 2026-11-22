"""CSV writing and reading: the two lender tape formats and the three outside record files."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import date

from onepledge_datagen.application.model import TapeFile
from onepledge_datagen.domain.entities import ContractRecord, PaymentRecord, TitleRecord
from onepledge_datagen.domain.errors import DataSetUnreadableError
from onepledge_datagen.domain.text_values import MAX_INTEGER, IntegerIssue, parse_integer_text, parse_iso_date

TITLE_HEADER = ("vin", "state", "title_status", "lienholder_name", "lien_recorded_on")
PAYMENT_HEADER = ("payment_id", "vin", "paid_on", "amount_cents", "deposit_bank_code")
CONTRACT_HEADER = ("contract_id", "vin", "lender_name", "amount_cents", "signed_on", "document_sha256")
_NEEDS_QUOTES = (",", '"', "\r", "\n")


def _cell(value: str, always_quote: bool) -> str:
    if (always_quote and value) or any(mark in value for mark in _NEEDS_QUOTES):
        return '"' + value.replace('"', '""') + '"'
    return value


def render_line(cells: Sequence[str], quoted_columns: frozenset[int] = frozenset()) -> str:
    """One CSV record without its line ending."""
    return ",".join(_cell(value, index in quoted_columns) for index, value in enumerate(cells))


def render_tape(tape: TapeFile) -> bytes:
    """A tape exactly as the lender sends it: header, rows, and the lender's encoding and line ends."""
    fmt = tape.lender_format
    lines = [render_line(fmt.header)]
    lines.extend(render_line(row, fmt.always_quoted_columns) for row in tape.rows)
    text = fmt.line_ending.join(lines) + fmt.line_ending
    if tape.trailing_blank_line:
        text += fmt.line_ending
    return (("﻿" if fmt.byte_order_mark else "") + text).encode("utf-8")


def _render_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> bytes:
    return ("\n".join([render_line(header), *(render_line(row) for row in rows)]) + "\n").encode("utf-8")


def _optional_date(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def render_titles(titles: Sequence[TitleRecord]) -> bytes:
    """titles.csv."""
    rows = [
        (t.vin, t.state, t.title_status, t.lienholder_name or "", _optional_date(t.lien_recorded_on))
        for t in titles
    ]
    return _render_table(TITLE_HEADER, rows)


def render_payments(payments: Sequence[PaymentRecord]) -> bytes:
    """payments.csv."""
    rows = [
        (p.payment_id, p.vin, p.paid_on.isoformat(), str(p.amount_cents), p.deposit_bank_code)
        for p in payments
    ]
    return _render_table(PAYMENT_HEADER, rows)


def render_contracts(contracts: Sequence[ContractRecord]) -> bytes:
    """contracts.csv."""
    rows = [
        (c.contract_id, c.vin, c.lender_name, str(c.amount_cents), c.signed_on.isoformat(), c.document_sha256)
        for c in contracts
    ]
    return _render_table(CONTRACT_HEADER, rows)


def _table(data: bytes, header: Sequence[str], name: str) -> list[list[str]]:
    try:
        rows = list(csv.reader(io.StringIO(data.decode("utf-8"), newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error):
        raise DataSetUnreadableError(f"{name} is not valid UTF-8 CSV.") from None
    if not rows or tuple(rows[0]) != tuple(header):
        raise DataSetUnreadableError(f"{name} does not start with the header {', '.join(header)}.")
    for line, row in enumerate(rows[1:], start=2):
        if len(row) != len(header):
            raise DataSetUnreadableError(f"{name} line {line} has {len(row)} cells instead of {len(header)}.")
    return rows[1:]


class _Cells:
    """The cells of one row of an outside records file; a bad cell names its file, line and column."""

    def __init__(self, name: str, header: Sequence[str], line: int, row: list[str]) -> None:
        self._name, self._header, self._line, self._row = name, header, line, row

    def _refuse(self, column: int, problem: str) -> DataSetUnreadableError:
        return DataSetUnreadableError(
            f"{self._name} line {self._line} column {self._header[column]} {problem}."
        )

    def text(self, column: int) -> str:
        return self._row[column]

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


def _rows(data: bytes, header: Sequence[str], name: str) -> list[_Cells]:
    return [_Cells(name, header, line, row) for line, row in enumerate(_table(data, header, name), start=2)]


def parse_titles(data: bytes) -> list[TitleRecord]:
    """Read titles.csv."""
    return [
        TitleRecord(c.text(0), c.text(1), c.text(2), c.text(3) or None, c.optional_day(4))
        for c in _rows(data, TITLE_HEADER, "titles.csv")
    ]


def parse_payments(data: bytes) -> list[PaymentRecord]:
    """Read payments.csv."""
    return [
        PaymentRecord(c.text(0), c.text(1), c.day(2), c.cents(3), c.text(4))
        for c in _rows(data, PAYMENT_HEADER, "payments.csv")
    ]


def parse_contracts(data: bytes) -> list[ContractRecord]:
    """Read contracts.csv."""
    return [
        ContractRecord(c.text(0), c.text(1), c.text(2), c.cents(3), c.day(4), c.text(5))
        for c in _rows(data, CONTRACT_HEADER, "contracts.csv")
    ]
