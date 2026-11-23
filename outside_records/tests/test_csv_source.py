"""Reading the generator's outside records files."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from onepledge_outside_records.domain.errors import OperatorError, RecordsRefusedError
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


# SPEC_DECISIONS 43.3 and 45: a file that is not the generator's CSV is one sentence naming it.
@pytest.mark.parametrize(
    ("name", "change", "fragment"),
    [
        (
            "titles.csv",
            lambda t: t.replace("vin,state", "vin,st", 1),
            "does not start with the header vin, state",
        ),
        ("titles.csv", lambda t: t + '"unterminated\n', "is not well formed CSV"),
    ],
)
def test_a_file_that_is_not_the_generators_csv_stops_with_one_sentence(
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


# SPEC_DECISIONS 109: every cell of every row of the three files is checked against the contract
# before anything is written; each problem is FILE line N, column NAME: PROBLEM.
DATE_RULE = "must be a date from 2000-01-01 to 2099-12-31"
CENTS_RULE = "must be a whole number of cents from 1 to 999,999,999,999,999,999"
CELL_PROBLEMS: list[tuple[str, Callable[[str], str], str]] = [
    (
        "titles.csv",
        lambda t: t.replace(second_line(t)[:17], second_line(t)[:16] + "O", 1),
        "titles.csv line 2, column vin: must match the VIN pattern of the contract",
    ),
    ("titles.csv", replace_cell(1, "nj"), "titles.csv line 2, column state: must be two upper case letters"),
    ("titles.csv", replace_cell(1, "NJX"), "titles.csv line 2, column state: must be two upper case letters"),
    (
        "titles.csv",
        replace_cell(2, "STOLEN"),
        "titles.csv line 2, column title_status: must be one of CLEAN, SALVAGE, JUNK",
    ),
    (
        "titles.csv",
        replace_cell(3, "B" * 201),
        "titles.csv line 2, column lienholder_name: must be at most 200 characters",
    ),
    (
        "titles.csv",
        replace_cell(3, "Harbor\x00Bank"),
        "titles.csv line 2, column lienholder_name: must not hold a control character",
    ),
    ("titles.csv", replace_cell(4, "2026-02-30"), f"titles.csv line 2, column lien_recorded_on: {DATE_RULE}"),
    ("titles.csv", replace_cell(4, "1999-12-31"), f"titles.csv line 2, column lien_recorded_on: {DATE_RULE}"),
    (
        "payments.csv",
        replace_cell(0, "not-a-uuid"),
        "payments.csv line 2, column payment_id: must be a UUID in lower case",
    ),
    ("payments.csv", replace_cell(2, "2025-13-01"), f"payments.csv line 2, column paid_on: {DATE_RULE}"),
    ("payments.csv", replace_cell(2, "2025-W01-1"), f"payments.csv line 2, column paid_on: {DATE_RULE}"),
    (
        "payments.csv",
        replace_cell(2, "\u0662\u0660\u0662\u0665-01-01"),
        f"payments.csv line 2, column paid_on: {DATE_RULE}",
    ),
    (
        "payments.csv",
        replace_cell(3, "\uff11\uff12"),
        f"payments.csv line 2, column amount_cents: {CENTS_RULE}",
    ),
    ("payments.csv", replace_cell(3, "9" * 5000), f"payments.csv line 2, column amount_cents: {CENTS_RULE}"),
    ("payments.csv", replace_cell(3, "0"), f"payments.csv line 2, column amount_cents: {CENTS_RULE}"),
    ("payments.csv", replace_cell(3, "+5"), f"payments.csv line 2, column amount_cents: {CENTS_RULE}"),
    (
        "payments.csv",
        replace_cell(4, "harbor bank 1"),
        "payments.csv line 2, column deposit_bank_code: must be 3 to 12 upper case letters",
    ),
    ("contracts.csv", replace_cell(3, "12.5"), f"contracts.csv line 2, column amount_cents: {CENTS_RULE}"),
    (
        "contracts.csv",
        replace_cell(3, "\u00b2345"),
        f"contracts.csv line 2, column amount_cents: {CENTS_RULE}",
    ),
    (
        "contracts.csv",
        replace_cell(2, "A" * 201),
        "contracts.csv line 2, column lender_name: must be at most 200 characters",
    ),
    (
        "contracts.csv",
        replace_cell(2, "A" * 5000),
        "contracts.csv line 2, column lender_name: must be at most 200 characters",
    ),
    (
        "contracts.csv",
        replace_cell(2, "Summit\x07Credit"),
        "contracts.csv line 2, column lender_name: must not hold a control character",
    ),
    (
        "contracts.csv",
        replace_cell(5, "A" * 64),
        "contracts.csv line 2, column document_sha256: must be 64 lower case hexadecimal digits",
    ),
    (
        "contracts.csv",
        replace_cell(5, "a" * 63),
        "contracts.csv line 2, column document_sha256: must be 64 lower case hexadecimal digits",
    ),
    ("payments.csv", lambda t: t + "extra,cells\n", "payments.csv line {last}: has 2 cells instead of 5"),
]


@pytest.mark.parametrize(("name", "change", "problem"), CELL_PROBLEMS, ids=[p for _, _, p in CELL_PROBLEMS])
def test_a_cell_outside_the_contract_is_named_by_file_line_and_column(
    tmp_path: Path,
    copy_records: Callable[[Path], Path],
    name: str,
    change: Callable[[str], str],
    problem: str,
) -> None:
    directory = copy_records(tmp_path)
    edit(directory, name, change)
    last = len((directory / "outside_records" / name).read_text(encoding="utf-8").splitlines())
    with pytest.raises(RecordsRefusedError) as raised:
        CsvRecordsSource().load(directory)
    assert raised.value.problems == (problem.format(last=last),)
    assert raised.value.sentence == "The outside records hold 1 problem; nothing was loaded."


def test_review_4_failure_1_every_problem_of_every_file_is_named_before_anything_is_loaded(
    tmp_path: Path, copy_records: Callable[[Path], Path]
) -> None:
    """Review 4 failure 1: these four cells were loaded and the service then broke its contract."""
    directory = copy_records(tmp_path)
    edit(directory, "titles.csv", replace_cell(1, "nj"))
    edit(directory, "titles.csv", replace_cell(3, "B" * 201))
    edit(directory, "contracts.csv", replace_cell(2, "A" * 201))
    edit(directory, "payments.csv", replace_cell(4, "harbor bank 1"))
    with pytest.raises(RecordsRefusedError) as raised:
        CsvRecordsSource().load(directory)
    assert raised.value.problems == (
        "titles.csv line 2, column state: must be two upper case letters",
        "titles.csv line 2, column lienholder_name: must be at most 200 characters",
        "payments.csv line 2, column deposit_bank_code: must be 3 to 12 upper case letters",
        "contracts.csv line 2, column lender_name: must be at most 200 characters",
    )


def _repeat_first_row(text: str) -> str:
    lines = text.splitlines(keepends=True)
    return "".join([*lines, lines[1]])


@pytest.mark.parametrize(
    ("name", "column"),
    [("titles.csv", "vin"), ("payments.csv", "payment_id"), ("contracts.csv", "contract_id")],
)
def test_a_repeated_key_inside_a_file_is_named_with_the_line_it_repeats(
    tmp_path: Path, copy_records: Callable[[Path], Path], name: str, column: str
) -> None:
    directory = copy_records(tmp_path)
    edit(directory, name, _repeat_first_row)
    last = len((directory / "outside_records" / name).read_text(encoding="utf-8").splitlines())
    with pytest.raises(RecordsRefusedError) as raised:
        CsvRecordsSource().load(directory)
    assert raised.value.problems == (f"{name} line {last}, column {column}: repeats the {column} of line 2",)


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
    assert raised.value.sentence == "outside_records/titles.csv is a link out of the directory."


# SPEC_DECISIONS 110: each CSV is a regular file inside the directory with no link on the way,
# refused by name before it is opened; review 4 quality 6 (seed read a FIFO forever).
def _fifo(directory: Path) -> None:
    payments = directory / "outside_records" / "payments.csv"
    payments.unlink()
    os.mkfifo(payments)


def _folder_linked_out(directory: Path) -> None:
    folder = directory / "outside_records"
    shutil.move(folder, directory.parent / "elsewhere")
    folder.symlink_to(directory.parent / "elsewhere")


def _linked_inside(directory: Path) -> None:
    contracts = directory / "outside_records" / "contracts.csv"
    contracts.rename(directory / "contracts_copy.csv")
    contracts.symlink_to(directory / "contracts_copy.csv")


def _a_directory(directory: Path) -> None:
    titles = directory / "outside_records" / "titles.csv"
    titles.unlink()
    titles.mkdir()


def _too_large(directory: Path) -> None:
    os.truncate(directory / "outside_records" / "payments.csv", 256 * 1024 * 1024 + 1)


NOT_REGULAR: dict[str, tuple[Callable[[Path], None], str]] = {
    "fifo": (_fifo, "outside_records/payments.csv is not a regular file."),
    "folder linked out": (_folder_linked_out, "outside_records is a link out of the directory."),
    "linked inside": (_linked_inside, "outside_records/contracts.csv is not a regular file."),
    "a directory": (_a_directory, "outside_records/titles.csv is a directory where a file is expected."),
    "too large": (_too_large, "outside_records/payments.csv is larger than 256 MiB."),
}


@pytest.mark.timeout(30)
@pytest.mark.parametrize("variant", sorted(NOT_REGULAR))
def test_a_path_that_is_not_a_regular_file_inside_is_refused_by_name(
    tmp_path: Path, copy_records: Callable[[Path], Path], variant: str
) -> None:
    change, sentence = NOT_REGULAR[variant]
    directory = copy_records(tmp_path / "data")
    change(directory)
    with pytest.raises(OperatorError) as raised:
        CsvRecordsSource().load(directory)
    assert raised.value.sentence == sentence
