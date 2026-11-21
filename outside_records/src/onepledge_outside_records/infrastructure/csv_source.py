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
    rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    if not rows or tuple(rows[0]) != HEADERS[name] or any(len(row) != len(HEADERS[name]) for row in rows[1:]):
        raise OperatorError(f"{path} does not have the outside records layout.", "INVALID_INPUT")
    return rows[1:]


def _vin(text: str, name: str) -> str:
    if not VIN_PATTERN.fullmatch(text):
        raise OperatorError(f"{name} holds a VIN that does not match the contract pattern.", "INVALID_INPUT")
    return text


def _optional_date(text: str) -> date | None:
    return date.fromisoformat(text) if text else None


class CsvRecordsSource:
    """The three CSV files of a data directory."""

    def load(self, data_dir: Path) -> RecordSet:
        """Parse and validate every row."""
        try:
            titles = tuple(
                TitleRecord(_vin(r[0], "titles.csv"), r[1], r[2], r[3] or None, _optional_date(r[4]))
                for r in _rows(data_dir, "titles.csv")
            )
            payments = tuple(
                Payment(r[0], _vin(r[1], "payments.csv"), date.fromisoformat(r[2]), int(r[3]), r[4])
                for r in _rows(data_dir, "payments.csv")
            )
            contracts = tuple(
                Contract(r[0], _vin(r[1], "contracts.csv"), r[2], int(r[3]), date.fromisoformat(r[4]), r[5])
                for r in _rows(data_dir, "contracts.csv")
            )
        except ValueError:
            raise OperatorError(
                "An outside records file holds a malformed date or amount.", "INVALID_INPUT"
            ) from None
        return RecordSet(titles, payments, contracts)
