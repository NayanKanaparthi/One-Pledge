"""Reading the generator's outside records files."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.csv_source import CsvRecordsSource

pytestmark = pytest.mark.integration


def data_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) - 1


def test_the_example_records_load_completely(example_dir: Path, example_records: Path) -> None:
    records = CsvRecordsSource().load(example_dir)
    assert len(records.titles) == data_lines(example_records / "titles.csv")
    assert len(records.payments) == data_lines(example_records / "payments.csv")
    assert len(records.contracts) == data_lines(example_records / "contracts.csv")
    assert all(isinstance(p.paid_on, date) and p.amount_cents > 0 for p in records.payments)
    assert any(t.lienholder_name is not None and t.lienholder_name.endswith(" ") for t in records.titles)


def edit(directory: Path, name: str, change: Callable[[str], str]) -> None:
    path = directory / "outside_records" / name
    path.write_text(change(path.read_text(encoding="utf-8")), encoding="utf-8")


def second_line(text: str) -> str:
    return text.splitlines()[1]


@pytest.mark.parametrize(
    ("name", "change", "fragment"),
    [
        ("titles.csv", lambda t: t.replace("vin,state", "vin,st", 1), "layout"),
        ("payments.csv", lambda t: t + "extra,cells\n", "layout"),
        ("titles.csv", lambda t: t.replace(second_line(t)[:17], second_line(t)[:16] + "O", 1), "VIN"),
        ("payments.csv", lambda t: t.replace(second_line(t).split(",")[2], "2025-13-01", 1), "malformed"),
        ("contracts.csv", lambda t: t.replace(second_line(t).split(",")[3], "12.5", 1), "malformed"),
    ],
)
def test_malformed_files_stop_with_one_sentence(
    tmp_path: Path,
    copy_records: Callable[[Path], Path],
    name: str,
    change: Callable[[str], str],
    fragment: str,
) -> None:
    directory = copy_records(tmp_path)
    edit(directory, name, change)
    with pytest.raises(OperatorError, match=fragment) as raised:
        CsvRecordsSource().load(directory)
    assert "\n" not in raised.value.sentence


def test_a_file_with_only_its_header_holds_no_records(
    tmp_path: Path, copy_records: Callable[[Path], Path]
) -> None:
    directory = copy_records(tmp_path)
    edit(directory, "contracts.csv", lambda t: t.splitlines()[0] + "\n")
    assert CsvRecordsSource().load(directory).contracts == ()


def test_a_missing_file_names_the_path(tmp_path: Path, copy_records: Callable[[Path], Path]) -> None:
    directory = copy_records(tmp_path)
    (directory / "outside_records" / "payments.csv").unlink()
    with pytest.raises(OperatorError, match=r"payments\.csv cannot be read"):
        CsvRecordsSource().load(directory)


def test_bytes_that_are_not_utf8_are_refused(tmp_path: Path, copy_records: Callable[[Path], Path]) -> None:
    directory = copy_records(tmp_path)
    (directory / "outside_records" / "titles.csv").write_bytes(b"vin,state\n\xff\xfe\n")
    with pytest.raises(OperatorError, match="cannot be read"):
        CsvRecordsSource().load(directory)
