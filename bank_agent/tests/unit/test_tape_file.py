"""Whole file validation, raw rows and fingerprints of an upload (SPEC 10.1 step 2, 10.2)."""

from __future__ import annotations

import hashlib
import json

import pytest

from bank_testkit.tapes import LAKESIDE_HEADER, SUMMIT_HEADER, lakeside_tape, summit_row, summit_tape
from onepledge_bank_agent.domain.enums import TapeKind
from onepledge_bank_agent.domain.errors import (
    TapeEmptyError,
    TapeHeaderInvalidError,
    TapeMalformedError,
    TapeNotUtf8Error,
    TapeRejectedError,
    TapeTooLargeError,
)
from onepledge_bank_agent.domain.tape_file import MAX_TAPE_BYTES, TapeFileReader, sanitise_file_name

pytestmark = pytest.mark.unit

READER = TapeFileReader()


def read(data: bytes, kind: TapeKind = TapeKind.PLEDGE) -> object:
    return READER.read(data, kind)


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b"", TapeEmptyError),
        (b"\n\n", TapeEmptyError),
        (summit_tape([]), TapeEmptyError),
        ("caf\xe9".encode("latin-1"), TapeNotUtf8Error),
        (b"PK\x03\x04\x14\x00\x00\x00\x08\x00\xff\xfe", TapeNotUtf8Error),
        (b"Loan #,VIN\nSAF-1,\x00\n", TapeMalformedError),
        (b'Loan #,VIN\n"SAF-1,1HGCM82633A004352\n', TapeMalformedError),
        (b'Loan #,VIN\n"SAF"-1,1HGCM82633A004352\n', TapeMalformedError),
        (b",VIN,Cust ID,Orig Date,Orig Amt,Curr Bal\nx,y,z,a,b,c\n", TapeHeaderInvalidError),
        (b"Loan #,VIN,VIN,Cust ID,Orig Date,Orig Amt,Curr Bal\nx,y,y,z,a,b,c\n", TapeHeaderInvalidError),
        (
            b"Loan #,VIN,vehicle_vin,Cust ID,Orig Date,Orig Amt,Curr Bal\nx,y,y,z,a,b,c\n",
            TapeHeaderInvalidError,
        ),
        (b"Loan #,Cust ID,Orig Date,Orig Amt,Curr Bal\nx,z,a,b,c\n", TapeHeaderInvalidError),
        (
            b"Loan #,VIN,Cust ID,Orig Date,Orig Amt,Curr Bal,__extra_1\nx,y,z,a,b,c,d\n",
            TapeHeaderInvalidError,
        ),
        (
            b"Loan #,VIN,Cust ID,Orig Date,Orig Amt,Curr Bal,Make,make\nx,y,z,a,b,c,d,e\n",
            TapeHeaderInvalidError,
        ),
    ],
)
def test_each_whole_file_failure_has_its_code(data: bytes, error: type[TapeRejectedError]) -> None:
    with pytest.raises(error):
        read(data)


def test_size_limits() -> None:
    with pytest.raises(TapeTooLargeError):
        read(b"a" * (MAX_TAPE_BYTES + 1))
    rows = [["SAF-1", "V", "C", "D", "1", "1"] for _ in range(20_001)]
    with pytest.raises(TapeTooLargeError):
        read(summit_tape(rows, SUMMIT_HEADER[:6]))
    read(summit_tape(rows[:20_000], SUMMIT_HEADER[:6]))
    with pytest.raises(TapeTooLargeError):
        read(summit_tape([summit_row(borrower="C" * 513)]))
    read(summit_tape([summit_row(borrower="C" * 512)]))
    wide = [*SUMMIT_HEADER, *(f"c{i}" for i in range(53))]
    with pytest.raises(TapeTooLargeError):
        read(summit_tape([summit_row() + [""] * 53], wide))
    with pytest.raises(TapeTooLargeError):
        read(summit_tape([summit_row(borrower="C" * 200_000)]))


def test_a_release_header_needs_only_the_vin() -> None:
    parsed = READER.read(b"VIN\n1HGCM82633A004352\n", TapeKind.RELEASE)
    assert parsed.header_map.columns == {"vin": "VIN"}
    with pytest.raises(TapeHeaderInvalidError):
        read(b"VIN\n1HGCM82633A004352\n", TapeKind.PLEDGE)


def test_raw_rows_are_exact_and_numbered_after_blank_lines() -> None:
    data = summit_tape(
        [summit_row(vin=" 1hgcm82633a004352 "), [], summit_row(), ["TOTAL", "", "", "", "", "$1.00"]]
    )
    parsed = READER.read(data, TapeKind.PLEDGE)
    assert [row.row_number for row in parsed.rows] == [1, 2, 3]
    first = parsed.rows[0].payload
    assert first["VIN"] == " 1hgcm82633a004352 " and list(first) == list(SUMMIT_HEADER)
    assert "\ufeffLoan #" not in first and "Loan #" in first
    assert set(parsed.rows[2].payload) == set(SUMMIT_HEADER[:6])
    assert parsed.file_sha256 == hashlib.sha256(data).hexdigest()


def test_cells_beyond_the_header_are_kept_as_extras() -> None:
    parsed = READER.read(
        b"Loan #,VIN,Cust ID,Orig Date,Orig Amt,Curr Bal\na,b,c,d,e,f,g,h\n", TapeKind.PLEDGE
    )
    assert parsed.rows[0].payload["__extra_1"] == "g" and parsed.rows[0].payload["__extra_2"] == "h"


def test_row_and_rows_hashes_follow_spec_10_2() -> None:
    parsed = READER.read(
        lakeside_tape([["a", "b", "c", "d", "e", "f", "", "", "", "", "", ""]] * 2), TapeKind.PLEDGE
    )
    expected_rows = []
    for row in parsed.rows:
        canonical = json.dumps(dict(row.payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        expected_rows.append(hashlib.sha256(canonical.encode()).hexdigest())
        assert row.row_sha256 == expected_rows[-1]
    assert parsed.rows_sha256 == hashlib.sha256("\n".join(expected_rows).encode()).hexdigest()
    assert list(parsed.rows[0].payload) == list(LAKESIDE_HEADER)


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\tapes\\S01 harbor (final).csv", "S01_harbor__final_.csv"),
        ("tape.csv", "tape.csv"),
        ("..", "unnamed"),
        ("", "unnamed"),
        ("a/b/", "unnamed"),
        ("é.csv", "_.csv"),
        ("x" * 300 + ".csv", "x" * 255),
    ],
)
def test_file_names_are_reduced_to_a_safe_base_name(name: str, stored: str) -> None:
    assert sanitise_file_name(name) == stored
