"""Verbatim raw storage (SPEC_DECISIONS 25) over every tape of the `full` data set.

Each tape is uploaded through `IngestTape`. The test then reads only the stored columns
and, without product code, rebuilds the uploaded file byte for byte, recomputes every
row hash from `raw_text`, re-parses the header and every record and rebuilds the
derived `raw_payload`. Summit files carry a BOM, CRLF line ends, a TOTAL row and a
trailing blank line; Lakeside files have none of these.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import pytest

from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase, drop, new_bank_database, provision_and_migrate
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.outside import ScriptedOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from onepledge_bank_agent.domain.enums import TapeKind

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Upload:
    """One stored tape and the file it came from."""

    database: BankDatabase
    submission_id: uuid.UUID
    file: Path
    facility_code: str


def no_lookups(request: httpx.Request) -> httpx.Response:
    raise AssertionError("intake never calls outside records")


@pytest.fixture(scope="module")
def uploads(full_data: Path) -> Iterator[list[Upload]]:
    steps = json.loads((full_data / "manifest.json").read_text(encoding="utf-8"))["steps"]
    databases: dict[str, BankDatabase] = {}
    found: list[Upload] = []
    doubles = InProcessDoubles()
    try:
        for step in steps:
            bank = step["bank_code"]
            if bank not in databases:
                databases[bank] = new_bank_database(bank.lower())
                provision_and_migrate(databases[bank])
                seed_reference(databases[bank], full_data, bank)
            with BankAgentHarness(
                databases[bank], Services(ScriptedOutsideRecords(no_lookups), doubles)
            ) as harness:
                file = full_data / step["file"]
                tape = harness.ingest(
                    file,
                    step["facility_code"],
                    TapeKind(step["kind"]),
                    date.fromisoformat(step["as_of_date"]),
                )
            found.append(Upload(databases[bank], tape.submission_id, file, step["facility_code"]))
        yield found
    finally:
        doubles.close()
        for database in databases.values():
            drop(database)


def stored(upload: Upload) -> tuple[tuple[object, ...], list[tuple[object, ...]]]:
    with upload.database.connect("admin") as connection:
        tape = connection.execute(
            "SELECT has_bom, header_text, header_cells, trailer_text, file_sha256, rows_sha256, row_count "
            "FROM tape_submissions WHERE submission_id = %s",
            (upload.submission_id,),
        ).fetchone()
        rows = connection.execute(
            "SELECT row_number, raw_text, raw_cells, raw_payload, row_sha256 FROM loans_raw "
            "WHERE submission_id = %s ORDER BY row_number",
            (upload.submission_id,),
        ).fetchall()
    assert tape is not None
    return tape, rows


def parse(text: str) -> list[list[str]]:
    """The records of a text, read the way any CSV reader reads it, blank lines dropped."""
    return [record for record in csv.reader(io.StringIO(text, newline=""), strict=True) if record]


def test_every_tape_of_both_formats_is_stored(uploads: list[Upload]) -> None:
    summit = [u for u in uploads if u.facility_code.endswith("SUMMIT")]
    lakeside = [u for u in uploads if u.facility_code.endswith("LAKESIDE")]
    assert len(uploads) == 15 and summit and lakeside


def test_the_stored_columns_rebuild_each_uploaded_file_byte_for_byte(uploads: list[Upload]) -> None:
    for upload in uploads:
        (has_bom, header_text, _, trailer_text, file_sha256, _, _), rows = stored(upload)
        assert isinstance(header_text, str) and isinstance(trailer_text, str)
        body = header_text + "".join(str(row[1]) for row in rows) + trailer_text
        rebuilt = (b"\xef\xbb\xbf" if has_bom else b"") + body.encode("utf-8")
        original = upload.file.read_bytes()
        assert rebuilt == original, upload.file.name
        assert hashlib.sha256(rebuilt).hexdigest() == file_sha256


def test_row_hashes_are_the_hashes_of_the_verbatim_text(uploads: list[Upload]) -> None:
    for upload in uploads:
        (_, _, _, _, _, rows_sha256, row_count), rows = stored(upload)
        hashes = [hashlib.sha256(str(row[1]).encode("utf-8")).hexdigest() for row in rows]
        assert hashes == [row[4] for row in rows], upload.file.name
        assert hashlib.sha256("\n".join(hashes).encode()).hexdigest() == rows_sha256
        assert row_count == len(rows) and [row[0] for row in rows] == list(range(1, len(rows) + 1))


def test_cells_and_payload_are_what_the_stored_text_parses_to(uploads: list[Upload]) -> None:
    for upload in uploads:
        (_, header_text, header_cells, _, _, _, _), rows = stored(upload)
        assert parse(str(header_text)) == [header_cells]
        for _, raw_text, raw_cells, raw_payload, _ in rows:
            assert parse(str(raw_text)) == [raw_cells], upload.file.name
            assert isinstance(header_cells, list) and isinstance(raw_cells, list)
            payload = dict(zip(header_cells, raw_cells, strict=False))
            payload.update({f"__extra_{i}": c for i, c in enumerate(raw_cells[len(header_cells) :], 1)})
            assert raw_payload == payload


def test_the_lender_formats_differ_as_the_spec_describes(uploads: list[Upload]) -> None:
    totals = 0
    for upload in uploads:
        (has_bom, header_text, _, trailer_text, _, _, _), rows = stored(upload)
        texts = [str(row[1]) for row in rows]
        if upload.facility_code.endswith("SUMMIT"):
            assert has_bom and str(header_text).endswith("\r\n") and trailer_text == "\r\n"
            assert all(text.endswith("\r\n") for text in texts)
            totals += parse(texts[-1])[0][0] == "TOTAL"
        else:
            assert not has_bom and str(header_text).endswith("\n") and "\r" not in "".join(texts)
    assert totals > 0, "some Summit tapes end with a TOTAL row"
