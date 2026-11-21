"""Builders of tape files in the two lender formats of SPEC 5.9, for tests."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

SUMMIT_HEADER = (
    "Loan #",
    "VIN",
    "Cust ID",
    "Orig Date",
    "Orig Amt",
    "Curr Bal",
    "APR",
    "Term",
    "Year",
    "Make",
    "Model",
    "State",
)
LAKESIDE_HEADER = (
    "loan_id", "vehicle_vin", "borrower_id", "origination_date", "original_amount", "current_balance",
    "apr_pct", "term_months", "vehicle_year", "vehicle_make", "vehicle_model", "title_state",
)  # fmt: skip


def summit_tape(rows: Sequence[Sequence[str]], header: Sequence[str] = SUMMIT_HEADER) -> bytes:
    """A Summit style file: UTF-8 with BOM, CRLF line ends, every cell as given."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def lakeside_tape(rows: Sequence[Sequence[str]], header: Sequence[str] = LAKESIDE_HEADER) -> bytes:
    """A Lakeside style file: UTF-8 without BOM, LF line ends."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


SUMMIT_DEFAULTS = {
    "loan_number": "SAF-1234567-01",
    "vin": "1HGCM82633A004352",
    "borrower": "C1234567",
    "origination": "03/15/2025",
    "original": "$23,450.17",
    "balance": "$18,000.00",
}
LAKESIDE_DEFAULTS = {
    "loan_number": "LMC12345678",
    "vin": "1HGCM82633A004352",
    "borrower": "LK-123456",
    "origination": "2025-03-15",
    "original": "23450.17",
    "balance": "18000.00",
}
_VEHICLE = ["14.9", "60", "2003", "Honda", "Accord", "NJ"]
_ORDER = ("loan_number", "vin", "borrower", "origination", "original", "balance")


def summit_row(**cells: str) -> list[str]:
    """One Summit data row: the defaults above with the given cells replaced; vehicle columns fixed."""
    values = {**SUMMIT_DEFAULTS, **cells}
    return [values[name] for name in _ORDER] + _VEHICLE


def lakeside_row(**cells: str) -> list[str]:
    """One Lakeside data row."""
    values = {**LAKESIDE_DEFAULTS, **cells}
    return [values[name] for name in _ORDER] + _VEHICLE
