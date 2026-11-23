"""The stored forms of a tape and how they are proven intact (SPEC 14.6, SPEC_DECISIONS 25 rules 5 and 6).

A file upload is intact when every row hash is the SHA-256 of its verbatim text, the
rows hash and the row count agree, re-parsing the header text and every row text gives
the stored cells, the stored payload is what the cells give, and the header, the row
texts and the trailer rebuild bytes whose SHA-256 is the file hash. A release
instruction is intact when its rows have the fixed one cell shape and the canonical
instruction document rebuilt from them hashes to the file hash.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from onepledge_bank_agent.domain.canonical_json import JsonValue, canonical_json, sha256_hex
from onepledge_bank_agent.domain.enums import TapeKind, TapeSource
from onepledge_bank_agent.domain.errors import DomainError
from onepledge_bank_agent.domain.tape_file import (
    RawRow,
    TapeLayout,
    raw_payload,
    reconstruct,
    row_sha256,
    rows_sha256,
    split_records,
)

INSTRUCTION_COLUMN = "loan_id"
INSTRUCTION_FILE_NAME = "release-instruction.json"
INSTRUCTION_LAYOUT = TapeLayout(False, INSTRUCTION_COLUMN + "\n", (INSTRUCTION_COLUMN,), "")


@dataclass(frozen=True)
class StoredRow:
    """One `loans_raw` row in all its stored forms."""

    row_number: int
    raw_text: str
    raw_cells: tuple[str, ...]
    raw_payload: Mapping[str, JsonValue]
    row_sha256: str


@dataclass(frozen=True)
class StoredTape:
    """One submission's stored facts and rows, as deep verification reads them."""

    submission_id: uuid.UUID
    source: TapeSource
    kind: TapeKind
    facility_id: uuid.UUID
    as_of_date: date
    file_sha256: str
    rows_sha256: str
    row_count: int
    layout: TapeLayout
    rows: tuple[StoredRow, ...]


@dataclass(frozen=True)
class ReleaseInstruction:
    """A release instruction stored like an upload: its document hash, layout and one row per loan."""

    file_sha256: str
    rows_sha256: str
    layout: TapeLayout
    rows: tuple[RawRow, ...]


def instruction_document(as_of_date: date, facility_id: uuid.UUID, loan_ids: Sequence[uuid.UUID]) -> bytes:
    """The canonical JSON `{"as_of_date", "facility_id", "loan_ids": sorted}` that stands as the file."""
    document: dict[str, JsonValue] = {
        "as_of_date": as_of_date.isoformat(),
        "facility_id": str(facility_id),
        "loan_ids": sorted(str(loan_id) for loan_id in loan_ids),
    }
    return canonical_json(document)


def release_instruction(
    as_of_date: date, facility_id: uuid.UUID, loan_ids: Sequence[uuid.UUID]
) -> ReleaseInstruction:
    """The stored forms of a release instruction (SPEC_DECISIONS 25 rule 5), one row per loan id in order."""
    cells = sorted(str(loan_id) for loan_id in set(loan_ids))
    rows = tuple(
        RawRow(number, cell + "\n", (cell,), {INSTRUCTION_COLUMN: cell}, row_sha256(cell + "\n"))
        for number, cell in enumerate(cells, start=1)
    )
    document = instruction_document(as_of_date, facility_id, [uuid.UUID(cell) for cell in cells])
    return ReleaseInstruction(
        sha256_hex(document), rows_sha256(row.row_sha256 for row in rows), INSTRUCTION_LAYOUT, rows
    )


def _parsed_cells(text: str) -> tuple[str, ...] | None:
    """The cells of the one record a verbatim text holds, or None when it holds anything else."""
    try:
        records, trailer = split_records(text)
    except DomainError:
        return None
    if len(records) != 1 or trailer:
        return None
    return records[0].cells


def _upload_intact(tape: StoredTape) -> bool:
    layout = tape.layout
    if _parsed_cells(layout.header_text) != layout.header_cells:
        return False
    for row in tape.rows:
        if _parsed_cells(row.raw_text) != row.raw_cells:
            return False
        if dict(row.raw_payload) != dict(raw_payload(layout.header_cells, row.raw_cells)):
            return False
    rebuilt = reconstruct(layout, (row.raw_text for row in tape.rows))
    return sha256_hex(rebuilt) == tape.file_sha256


def _instruction_intact(tape: StoredTape) -> bool:
    if tape.layout != INSTRUCTION_LAYOUT:
        return False
    loan_ids: list[uuid.UUID] = []
    for row in tape.rows:
        if len(row.raw_cells) != 1 or row.raw_text != row.raw_cells[0] + "\n":
            return False
        if dict(row.raw_payload) != {INSTRUCTION_COLUMN: row.raw_cells[0]}:
            return False
        try:
            loan_ids.append(uuid.UUID(row.raw_cells[0]))
        except ValueError:
            return False
    document = instruction_document(tape.as_of_date, tape.facility_id, loan_ids)
    return sha256_hex(document) == tape.file_sha256


def tape_is_intact(tape: StoredTape) -> bool:
    """Every stored form of the submission agrees with the others and with the file hash."""
    rows = sorted(tape.rows, key=lambda row: row.row_number)
    if [row.row_number for row in rows] != list(range(1, len(rows) + 1)) or len(rows) != tape.row_count:
        return False
    if any(row_sha256(row.raw_text) != row.row_sha256 for row in rows):
        return False
    if rows_sha256(row.row_sha256 for row in rows) != tape.rows_sha256:
        return False
    ordered = replace(tape, rows=tuple(rows))
    if tape.source is TapeSource.API_INSTRUCTION:
        return _instruction_intact(ordered)
    return _upload_intact(ordered)
