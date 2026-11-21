"""CSV writing and reading: the two lender tape formats and the three outside record files."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import date

from onepledge_datagen.application.model import TapeFile
from onepledge_datagen.domain.entities import ContractRecord, PaymentRecord, TitleRecord
from onepledge_datagen.domain.errors import DataSetUnreadableError

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
        raise DataSetUnreadableError(f"{name} does not start with the expected header.")
    body = rows[1:]
    if any(len(row) != len(header) for row in body):
        raise DataSetUnreadableError(f"{name} has a row with the wrong number of cells.")
    return body


def parse_titles(data: bytes) -> list[TitleRecord]:
    """Read titles.csv."""
    try:
        return [
            TitleRecord(
                row[0], row[1], row[2], row[3] or None, date.fromisoformat(row[4]) if row[4] else None
            )
            for row in _table(data, TITLE_HEADER, "titles.csv")
        ]
    except ValueError:
        raise DataSetUnreadableError("titles.csv holds a malformed date.") from None


def parse_payments(data: bytes) -> list[PaymentRecord]:
    """Read payments.csv."""
    try:
        return [
            PaymentRecord(row[0], row[1], date.fromisoformat(row[2]), int(row[3]), row[4])
            for row in _table(data, PAYMENT_HEADER, "payments.csv")
        ]
    except ValueError:
        raise DataSetUnreadableError("payments.csv holds a malformed date or amount.") from None


def parse_contracts(data: bytes) -> list[ContractRecord]:
    """Read contracts.csv."""
    try:
        return [
            ContractRecord(row[0], row[1], row[2], int(row[3]), date.fromisoformat(row[4]), row[5])
            for row in _table(data, CONTRACT_HEADER, "contracts.csv")
        ]
    except ValueError:
        raise DataSetUnreadableError("contracts.csv holds a malformed date or amount.") from None
