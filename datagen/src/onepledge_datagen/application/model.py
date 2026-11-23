"""The in-memory data set handed to the file renderer, and the file layout (SPEC 5.3, 5.9)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from onepledge_datagen.domain.entities import ContractRecord, PaymentRecord, TitleRecord
from onepledge_datagen.domain.tape_format import LenderFormat

MANIFEST = "manifest.json"
REFERENCE = "reference.json"
CHECKSUMS = "checksums.json"
ANSWER_KEY = "answer_key/answer_key.json"
TITLES = "outside_records/titles.csv"
PAYMENTS = "outside_records/payments.csv"
CONTRACTS = "outside_records/contracts.csv"
EXAMPLE_DIR = "contract_example"
EXAMPLE_TAPE = f"{EXAMPLE_DIR}/example_tape_20.csv"
EXAMPLE_EXPECTED = f"{EXAMPLE_DIR}/example_tape_20.expected.json"
EXAMPLE_RECORDS: Mapping[str, str] = MappingProxyType(
    {
        TITLES: f"{EXAMPLE_DIR}/example_outside_records/titles.csv",
        PAYMENTS: f"{EXAMPLE_DIR}/example_outside_records/payments.csv",
        CONTRACTS: f"{EXAMPLE_DIR}/example_outside_records/contracts.csv",
    }
)


@dataclass(frozen=True)
class TapeFile:
    """One tape: where it goes, its lender format and its rows of cells in file order."""

    path: str
    lender_format: LenderFormat
    rows: tuple[tuple[str, ...], ...]
    trailing_blank_line: bool


@dataclass(frozen=True)
class DataSetModel:
    """Everything that is written, before rendering."""

    manifest: dict[str, object]
    reference: dict[str, object]
    answer_key: dict[str, object]
    tapes: tuple[TapeFile, ...]
    titles: tuple[TitleRecord, ...]
    payments: tuple[PaymentRecord, ...]
    contracts: tuple[ContractRecord, ...]


@dataclass(frozen=True)
class DataSetFiles:
    """What a data directory holds: each regular file's bytes and each directory, by relative path."""

    files: dict[str, bytes]
    directories: frozenset[str]
