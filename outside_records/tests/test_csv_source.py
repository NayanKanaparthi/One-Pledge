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


def replace_cell(column: int, value: str) -> Callable[[str], str]:
    """Replace one cell of the first data row."""

    def change(text: str) -> str:
        lines = text.splitlines(keepends=True)
        cells = lines[1].rstrip("\n").split(",")
        cells[column] = value
        lines[1] = ",".join(cells) + "\n"
        return "".join(lines)

    return change


# SPEC_DECISIONS 43.3 and 45: a malformed file names the file and the problem; digits are ASCII.
@pytest.mark.parametrize(
    ("name", "change", "fragment"),
    [
        (
            "titles.csv",
            lambda t: t.replace("vin,state", "vin,st", 1),
            "does not start with the header vin, state",
        ),
        ("payments.csv", lambda t: t + "extra,cells\n", "line .* has 2 cells instead of 5"),
        (
            "titles.csv",
            lambda t: t.replace(second_line(t)[:17], second_line(t)[:16] + "O", 1),
            "titles.csv line 2 column vin",
        ),
        (
            "payments.csv",
            replace_cell(2, "2025-13-01"),
            "payments.csv line 2 column paid_on is not a YYYY-MM-DD date",
        ),
        ("payments.csv", replace_cell(2, "2025-W01-1"), "column paid_on is not a YYYY-MM-DD date"),
        (
            "payments.csv",
            replace_cell(2, "\u0662\u0660\u0662\u0665-01-01"),
            "column paid_on is not a YYYY-MM-DD date",
        ),
        (
            "contracts.csv",
            replace_cell(3, "12.5"),
            "contracts.csv line 2 column amount_cents is not a positive",
        ),
        ("contracts.csv", replace_cell(3, "\u00b2345"), "column amount_cents is not a positive whole number"),
        (
            "payments.csv",
            replace_cell(3, "\uff11\uff12"),
            "column amount_cents is not a positive whole number",
        ),
        ("payments.csv", replace_cell(3, "9" * 5000), "column amount_cents is not a positive whole number"),
        ("payments.csv", replace_cell(3, "0"), "column amount_cents is not a positive whole number"),
        ("payments.csv", replace_cell(3, "+5"), "column amount_cents is not a positive whole number"),
        ("titles.csv", replace_cell(4, "2026-02-30"), "titles.csv line 2 column lien_recorded_on"),
        ("titles.csv", lambda t: t + '"unterminated\n', "is not well formed CSV"),
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
    assert "Unexpected" not in raised.value.sentence


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


def test_a_file_that_links_out_of_the_data_directory_is_refused_unread(
    tmp_path: Path, copy_records: Callable[[Path], Path]
) -> None:
    """SPEC_DECISIONS 74: seed never follows a symbolic link out of the directory it was given."""
    directory = copy_records(tmp_path / "data")
    outside = tmp_path / "elsewhere.csv"
    outside.write_text((directory / "outside_records" / "titles.csv").read_text(encoding="utf-8"))
    titles = directory / "outside_records" / "titles.csv"
    titles.unlink()
    titles.symlink_to(outside)
    with pytest.raises(OperatorError) as raised:
        CsvRecordsSource().load(directory)
    assert raised.value.sentence == "outside_records/titles.csv links to a path outside the directory."
