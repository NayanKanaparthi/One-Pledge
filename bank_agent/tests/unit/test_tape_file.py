"""Whole file validation, verbatim raw rows and fingerprints of an upload (SPEC 10.1, SPEC_DECISIONS 25)."""

from __future__ import annotations

import csv
import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bank_testkit.tapes import LAKESIDE_HEADER, SUMMIT_HEADER, lakeside_tape, summit_row, summit_tape
from onepledge_bank_agent.domain import tape_file
from onepledge_bank_agent.domain.enums import TapeKind
from onepledge_bank_agent.domain.errors import (
    TapeEmptyError,
    TapeHeaderInvalidError,
    TapeMalformedError,
    TapeNotUtf8Error,
    TapeRejectedError,
    TapeTooLargeError,
)
from onepledge_bank_agent.domain.tape_file import (
    MAX_TAPE_BYTES,
    TapeFileReader,
    physical_lines,
    reconstruct,
    sanitise_file_name,
)

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
    assert dict(parsed.header_map.columns) == {"vin": 0}
    with pytest.raises(TapeHeaderInvalidError):
        read(b"VIN\n1HGCM82633A004352\n", TapeKind.PLEDGE)


def test_raw_rows_are_verbatim_and_numbered_after_blank_lines() -> None:
    data = summit_tape(
        [summit_row(vin=" 1hgcm82633a004352 "), [], summit_row(), ["TOTAL", "", "", "", "", "$1.00"]]
    )
    parsed = READER.read(data, TapeKind.PLEDGE)
    assert [row.row_number for row in parsed.rows] == [1, 2, 3]
    first = parsed.rows[0]
    assert first.cells[1] == " 1hgcm82633a004352 " and len(first.cells) == len(SUMMIT_HEADER)
    assert first.text.endswith("\r\n") and first.text.count("\r\n") == 1
    assert parsed.rows[1].text.startswith("\r\n"), "the blank line joins the next record"
    assert parsed.rows[2].cells == ("TOTAL", "", "", "", "", "$1.00")
    layout = parsed.layout
    assert layout.has_bom and layout.header_cells == SUMMIT_HEADER and layout.trailer_text == ""
    assert layout.header_text == ",".join(SUMMIT_HEADER) + "\r\n"
    assert first.payload["VIN"] == " 1hgcm82633a004352 " and "\ufeffLoan #" not in first.payload
    assert parsed.file_sha256 == hashlib.sha256(data).hexdigest()
    assert reconstruct(layout, (row.text for row in parsed.rows)) == data


def test_a_summit_file_with_a_totals_row_and_a_trailing_blank_line_rebuilds_exactly() -> None:
    data = (
        summit_tape([summit_row(), ["TOTAL", "", "", "", "", "$18,000.00", "", "", "", "", "", ""]]) + b"\r\n"
    )
    parsed = READER.read(data, TapeKind.PLEDGE)
    assert len(parsed.rows) == 2 and parsed.layout.trailer_text == "\r\n"
    assert reconstruct(parsed.layout, (row.text for row in parsed.rows)) == data


def test_a_quoted_cell_with_a_line_break_stays_inside_one_record() -> None:
    data = b'VIN,Note\n1HGCM82633A004352,"two\r\nlines, ""quoted"""\n\n\n5YJ3E1EA2KF317000,x'
    parsed = READER.read(data, TapeKind.RELEASE)
    assert [row.cells for row in parsed.rows] == [
        ("1HGCM82633A004352", 'two\r\nlines, "quoted"'),
        ("5YJ3E1EA2KF317000", "x"),
    ]
    assert parsed.rows[0].text == '1HGCM82633A004352,"two\r\nlines, ""quoted"""\n'
    assert parsed.rows[1].text == "\n\n5YJ3E1EA2KF317000,x" and parsed.layout.trailer_text == ""
    assert not parsed.layout.has_bom


@pytest.mark.parametrize("terminator", ["\n", "\r\n", "\r"])
def test_every_line_terminator_is_kept_and_mixed_files_rebuild(terminator: str) -> None:
    text = f"VIN{terminator}1HGCM82633A004352{terminator}{terminator}5YJ3E1EA2KF317000\r\n\n"
    parsed = READER.read(text.encode(), TapeKind.RELEASE)
    assert parsed.layout.header_text == f"VIN{terminator}"
    assert [row.text for row in parsed.rows] == [
        f"1HGCM82633A004352{terminator}",
        f"{terminator}5YJ3E1EA2KF317000\r\n",
    ]
    assert parsed.layout.trailer_text == "\n"
    assert reconstruct(parsed.layout, (row.text for row in parsed.rows)) == text.encode()


def test_cells_beyond_the_header_are_kept_in_order_and_named_extras_in_the_payload() -> None:
    parsed = READER.read(
        b"Loan #,VIN,Cust ID,Orig Date,Orig Amt,Curr Bal\na,b,c,d,e,f,g,h\n", TapeKind.PLEDGE
    )
    assert parsed.rows[0].cells == ("a", "b", "c", "d", "e", "f", "g", "h")
    assert parsed.rows[0].payload["__extra_1"] == "g" and parsed.rows[0].payload["__extra_2"] == "h"


def test_row_hashes_are_over_the_verbatim_text_and_the_rows_hash_joins_them() -> None:
    parsed = READER.read(
        lakeside_tape([["a", "b", "c", "d", "e", "f", "", "", "", "", "", ""]] * 2), TapeKind.PLEDGE
    )
    expected_rows = [hashlib.sha256(row.text.encode()).hexdigest() for row in parsed.rows]
    assert [row.row_sha256 for row in parsed.rows] == expected_rows
    assert parsed.rows_sha256 == hashlib.sha256("\n".join(expected_rows).encode()).hexdigest()
    assert list(parsed.rows[0].payload) == list(LAKESIDE_HEADER) and not parsed.layout.has_bom


CELL = st.text(alphabet=st.sampled_from(list('ab ,"\r\n\u00e9')), max_size=6)
TERMINATOR = st.sampled_from(["\n", "\r\n", "\r"])


def _quoted(cell: str) -> str:
    return '"' + cell.replace('"', '""') + '"'


@given(
    st.lists(
        st.tuples(st.lists(CELL, min_size=1, max_size=3), TERMINATOR, st.integers(0, 2)),
        min_size=1,
        max_size=6,
    ),
    st.booleans(),
    st.lists(st.sampled_from(["\n", "\r\n"]), max_size=3).map("".join),
)
def test_any_well_formed_file_rebuilds_exactly_and_each_record_reparses(
    records: list[tuple[list[str], str, int]], bom: bool, trailer: str
) -> None:
    text = "VIN,Note\n"
    expected: list[tuple[str, ...]] = []
    for cells, terminator, blanks in records:
        text += terminator * blanks + ",".join(_quoted(cell) for cell in cells) + terminator
        expected.append(tuple(cells))
    data = (b"\xef\xbb\xbf" if bom else b"") + (text + trailer).encode()
    parsed = READER.read(data, TapeKind.RELEASE)
    assert [row.cells for row in parsed.rows] == expected
    assert reconstruct(parsed.layout, (row.text for row in parsed.rows)) == data
    for row in parsed.rows:
        assert [cells for cells in csv.reader(physical_lines(row.text), strict=True) if cells] == [
            list(row.cells)
        ]


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\tapes\\S01 harbor (final).csv", "S01_harbor__final_.csv"),
        ("tape.csv", "tape.csv"),
        ("..", "unnamed"),
        ("", "unnamed"),
        ("._._", "unnamed"),
        ("???", "unnamed"),
        ("_", "unnamed"),
        ("._a", "._a"),
        ("a/b/", "unnamed"),
        ("é.csv", "_.csv"),
        ("x" * 300 + ".csv", "x" * 255),
    ],
)
def test_file_names_are_reduced_to_a_safe_base_name(name: str, stored: str) -> None:
    assert sanitise_file_name(name) == stored


def test_a_file_its_records_do_not_rebuild_is_refused_as_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reconstruction assertion of SPEC_DECISIONS 25 rule 1 is checked on every upload."""
    split = tape_file.split_records

    def losing_the_trailer(text: str) -> tuple[list[tape_file.SourceRecord], str]:
        records, _ = split(text)
        return records, ""

    data = summit_tape([summit_row()]) + b"\r\n"
    READER.read(data, TapeKind.PLEDGE)
    monkeypatch.setattr(tape_file, "split_records", losing_the_trailer)
    with pytest.raises(TapeMalformedError):
        READER.read(data, TapeKind.PLEDGE)
