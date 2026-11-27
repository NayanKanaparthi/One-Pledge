"""The command line: generate, verify, refusals and one sentence errors (SPEC 5.1, 19)."""

from __future__ import annotations

import csv
import errno
import io
import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from onepledge_datagen.application.invariants import InvariantChecker
from onepledge_datagen.infrastructure.directory import describe
from onepledge_datagen.interface.cli import main
from onepledge_datagen.interface.output import Console
from onepledge_testtools.command_lines import UnwrappingStream

pytestmark = pytest.mark.integration


def run(*argv: str) -> tuple[int, str, str]:
    """The command in process; error sentences naming long test paths are joined back into one line."""
    out, err = io.StringIO(), UnwrappingStream()
    code = main([*argv, "--wide"], Console(out, err))
    return code, out.getvalue(), err.getvalue()


def test_generate_prints_a_summary_and_verify_passes(tmp_path: Path) -> None:
    target = tmp_path / "data"
    code, out, err = run("generate", "--seed", "7", "--profile", "small", "--out", str(target))
    assert code == 0 and err == ""
    assert out.startswith("OnePledge data set: profile small, seed 7\n")
    assert "Invariants: all checks passed" in out and f"to {target}" in out
    code, out, _ = run("verify", "--data-dir", str(target))
    assert code == 0 and out.startswith("verify: OK (")


def test_generate_refuses_a_non_empty_directory_unless_forced(tmp_path: Path) -> None:
    target = tmp_path / "data"
    assert run("generate", "--seed", "7", "--profile", "example", "--out", str(target))[0] == 0
    code, _, err = run("generate", "--seed", "8", "--profile", "example", "--out", str(target))
    assert code == 1 and "not empty" in err and err.count("\n") == 1
    assert run("generate", "--seed", "8", "--profile", "example", "--out", str(target), "--force")[0] == 0
    assert json.loads((target / "manifest.json").read_text())["seed"] == 8


def test_force_never_replaces_a_folder_that_is_not_a_data_set(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("mine")
    code, _, err = run("generate", "--seed", "7", "--profile", "example", "--out", str(tmp_path), "--force")
    assert code == 1 and "does not hold a data set" in err
    assert (tmp_path / "keep.txt").read_text() == "mine"


def test_an_unknown_profile_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        main(["generate", "--seed", "1", "--profile", "nope", "--out", "x"])
    assert exited.value.code == 2
    assert capsys.readouterr().err.splitlines()[-1] == (
        "onepledge-datagen generate: argument --profile must be one of: full, small, example."
    )


def test_verify_reports_tampering(small_dir: Path, tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(small_dir, copy)
    titles = copy / "outside_records" / "titles.csv"
    lines = titles.read_text().splitlines(keepends=True)
    titles.write_text("".join(lines[:1] + lines[2:]))
    (copy / "stray.txt").write_text("x")
    code, _, err = run("verify", "--data-dir", str(copy))
    assert code == 1
    assert "outside_records/titles.csv does not match its checksum" in err
    assert "stray.txt is present but not listed" in err
    assert ", baseline: outcome HELD_LOCAL instead of LOCKED" in err
    assert "outside_records/titles.csv differs from what seed 20261117, profile small generates" in err
    assert "stray.txt is not part of what seed 20261117, profile small generates" in err


def test_verify_reports_a_wrong_answer_key(small_dir: Path, tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(small_dir, copy)
    key_path = copy / "answer_key" / "answer_key.json"
    key = json.loads(key_path.read_text())
    case = next(c for c in key["cases"] if c["case_type"] == "COPY_AMOUNT_CHANGED")
    case["rows"][1]["expected"]["match_kind"] = "EXACT"
    key_path.write_text(json.dumps(key))
    code, _, err = run("verify", "--data-dir", str(copy))
    assert code == 1 and "match LIKELY/ASSET instead of EXACT/ASSET" in err


def test_verify_of_a_missing_or_broken_directory_is_one_sentence(tmp_path: Path) -> None:
    code, _, err = run("verify", "--data-dir", str(tmp_path / "absent"))
    assert code == 1 and err.endswith("is not a directory.\n")
    (tmp_path / "manifest.json").write_text("{not json")
    code, _, err = run("verify", "--data-dir", str(tmp_path))
    assert code == 1 and err == "manifest.json: not valid JSON.\n"


def test_verify_checks_the_example_contract_files(example_dir: Path, tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(example_dir, copy)
    expected = copy / "contract_example" / "example_tape_20.expected.json"
    expected.write_text(expected.read_text().replace("PENDING", "HELD_LOCAL", 1))
    code, _, err = run("verify", "--data-dir", str(copy))
    assert code == 1 and "differs from the results of the oracle" in err


LAKESIDE_FIX = ["BORROWER_REF_NORMALISED"]


def test_the_key_declares_the_inherent_fixes_of_every_facility(small_dir: Path, example_dir: Path) -> None:
    small = json.loads((small_dir / "answer_key" / "answer_key.json").read_text())
    assert small["inherent_fixes"] == {
        "CALDER-SUMMIT": [],
        "HARBOR-LAKESIDE": LAKESIDE_FIX,
        "HARBOR-SUMMIT": [],
        "NORTHGATE-LAKESIDE": LAKESIDE_FIX,
        "NORTHGATE-SUMMIT": [],
    }
    example = json.loads((example_dir / "answer_key" / "answer_key.json").read_text())
    assert example["inherent_fixes"] == {"HARBOR-SUMMIT": []}


def tampered_key(source: Path, target: Path, change: Callable[[dict[str, Any]], None]) -> Path:
    shutil.copytree(source, target)
    key_path = target / "answer_key" / "answer_key.json"
    key = json.loads(key_path.read_text())
    change(key)
    key_path.write_text(json.dumps(key))
    return target


def test_verify_refuses_a_key_without_the_lakeside_inherent_fix(small_dir: Path, tmp_path: Path) -> None:
    def drop(key: dict[str, Any]) -> None:
        key["inherent_fixes"]["HARBOR-LAKESIDE"] = []

    code, _, err = run("verify", "--data-dir", str(tampered_key(small_dir, tmp_path / "copy", drop)))
    assert code == 1
    assert (
        "answer key inherent_fixes of HARBOR-LAKESIDE is none, its lender format causes "
        "BORROWER_REF_NORMALISED" in err
    )
    assert ", baseline: cleaning problems BORROWER_REF_NORMALISED instead of none" in err


def test_verify_refuses_an_inherent_fix_the_format_does_not_cause(small_dir: Path, tmp_path: Path) -> None:
    def add(key: dict[str, Any]) -> None:
        key["inherent_fixes"]["HARBOR-SUMMIT"] = ["WHITESPACE_TRIMMED"]

    code, _, err = run("verify", "--data-dir", str(tampered_key(small_dir, tmp_path / "copy", add)))
    assert code == 1
    assert "inherent_fixes of HARBOR-SUMMIT is WHITESPACE_TRIMMED, its lender format causes none" in err


def test_verify_refuses_a_key_that_omits_a_facility(small_dir: Path, tmp_path: Path) -> None:
    def omit(key: dict[str, Any]) -> None:
        del key["inherent_fixes"]["CALDER-SUMMIT"]

    code, _, err = run("verify", "--data-dir", str(tampered_key(small_dir, tmp_path / "copy", omit)))
    assert code == 1
    assert "reference.json has CALDER-SUMMIT, HARBOR-LAKESIDE" in err


@pytest.mark.parametrize(
    "value", [None, ["BORROWER_REF_NORMALISED"], {"HARBOR-SUMMIT": "WHITESPACE_TRIMMED"}]
)
def test_verify_refuses_a_malformed_inherent_fixes_map(
    small_dir: Path, tmp_path: Path, value: object
) -> None:
    def replace(key: dict[str, Any]) -> None:
        key["inherent_fixes"] = value

    code, _, err = run("verify", "--data-dir", str(tampered_key(small_dir, tmp_path / "copy", replace)))
    assert (code, err) == (1, "answer key has a malformed inherent_fixes map.\n")


def run_json(*argv: str) -> tuple[int, dict[str, Any], str]:
    code, out, err = run(*argv, "--json")
    document: dict[str, Any] = json.loads(out)
    return code, document, err


def test_an_output_path_under_a_file_is_one_sentence(tmp_path: Path) -> None:
    afile = tmp_path / "afile"
    afile.write_text("x")
    code, out, err = run("generate", "--seed", "1", "--profile", "example", "--out", str(afile / "x"))
    assert (code, out) == (1, "")
    target = afile / "x"
    assert (
        err == f"Cannot write the data set to {target}: a part of the path is a file; choose another --out.\n"
    )


# The plain words of SPEC_DECISIONS 23 d for each refusal, written here independently of the
# product's table, so a test compares the product with the decision and not with itself.
PLAIN_WORDS = {
    errno.EACCES: "permission denied",
    errno.EPERM: "permission denied",
    errno.EROFS: "the file system is read-only",
    errno.ENOSPC: "no space is left on the device",
    errno.ENOTDIR: "a part of the path is a file",
}
# A directory no process may create: on Linux `/sys` is sysfs, which refuses a new directory (EPERM,
# or EACCES for a user other than root); on macOS `/` is the sealed system volume (EROFS).
SYSTEM_REFUSED = Path("/sys/onepledge-test/data")


def _refusal(target: Path) -> OSError:
    """The error this operating system raises when a directory is made at `target`."""
    try:
        target.mkdir(parents=True)
    except OSError as error:
        return error
    target.rmdir()
    pytest.fail(f"{target} was created; this operating system refuses no part of it")


def test_a_path_the_system_refuses_is_one_sentence() -> None:
    """The sentence for the refusal this operating system actually raises, whichever it is."""
    refusal = _refusal(SYSTEM_REFUSED)
    assert refusal.errno in PLAIN_WORDS, f"an unexpected refusal: {refusal!r}"
    code, out, err = run("generate", "--seed", "1", "--profile", "example", "--out", str(SYSTEM_REFUSED))
    words = PLAIN_WORDS[refusal.errno]
    assert (code, out) == (1, "")
    assert err == f"Cannot write the data set to {SYSTEM_REFUSED}: {words}; choose another --out.\n"


@pytest.mark.parametrize("number", sorted(PLAIN_WORDS), ids=[errno.errorcode[n] for n in sorted(PLAIN_WORDS)])
def test_each_refusal_of_the_output_directory_is_one_sentence(
    number: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same sentence for each refusal, made deterministically whatever the OS and the user."""
    target = tmp_path / "out" / "data"

    def refuse(path: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False) -> None:
        raise OSError(number, os.strerror(number), str(path))

    monkeypatch.setattr(Path, "mkdir", refuse)
    code, out, err = run("generate", "--seed", "1", "--profile", "example", "--out", str(target))
    assert (code, out) == (1, "")
    assert err == f"Cannot write the data set to {target}: {PLAIN_WORDS[number]}; choose another --out.\n"


def test_a_missing_parent_is_created(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "data"
    assert run("generate", "--seed", "1", "--profile", "example", "--out", str(target))[0] == 0
    assert (target / "manifest.json").is_file()


def test_a_file_the_system_refuses_to_read_is_one_sentence(
    example_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(example_dir, copy)
    original = os.open

    def refuse(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        if Path(path).name == "manifest.json":
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return original(path, flags, mode)

    monkeypatch.setattr(os, "open", refuse)
    code, out, err = run("verify", "--data-dir", str(copy))
    assert (code, out, err) == (1, "", f"Cannot read the data set in {copy}: permission denied.\n")


@pytest.mark.parametrize(
    ("number", "words"),
    [
        *sorted(PLAIN_WORDS.items()),
        # An error the decision has no words for is the operating system's own description.
        (errno.EIO, os.strerror(errno.EIO).lower()),
    ],
)
def test_file_system_problems_are_described_in_plain_words(number: int, words: str) -> None:
    assert describe(OSError(number, os.strerror(number))) == words


def test_an_unexpected_error_is_one_sentence_and_debug_adds_the_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*arguments: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(InvariantChecker, "check", explode)
    argv = ("generate", "--seed", "1", "--profile", "example", "--out", str(tmp_path / "d"))
    code, out, err = run(*argv)
    record, sentence = err.splitlines()
    assert (code, out, sentence) == (1, "", "Unexpected error. Run with --debug for details.")
    # SPEC_DECISIONS 78: the last resort also writes one error record with the type and the frames.
    fields = json.loads(record)
    assert (fields["level"], fields["event"], fields["service"]) == (
        "error",
        "unhandled_exception",
        "datagen",
    )
    assert fields["exception_type"] == "RuntimeError" and " explode" in fields["stack"]
    assert "boom" not in record
    code, _, err = run(*argv, "--debug")
    # SPEC_DECISIONS 53.12: the type and the frames, never the message or a source line.
    assert code == 1 and err.startswith("RuntimeError, raised at:\n") and " explode\n" in err
    assert "boom" not in err and "Traceback" not in err and 'raise RuntimeError("boom")' not in err
    code, document, _ = run_json(*argv)
    assert (code, document["error"]["code"]) == (1, "UNEXPECTED_ERROR")


def test_generate_and_verify_print_one_json_document(tmp_path: Path) -> None:
    target = tmp_path / "data"
    code, document, err = run_json("generate", "--seed", "7", "--profile", "example", "--out", str(target))
    assert (code, err, document["ok"], document["profile"], document["seed"]) == (0, "", True, "example", 7)
    assert document["steps"][0]["rows"] == 20 and document["files"] == len(list(target.rglob("*.*")))
    code, document, _ = run_json("verify", "--data-dir", str(target))
    assert (code, document["ok"], document["rows_checked"]) == (0, True, 20)
    (target / "stray.txt").write_text("x")
    code, document, err = run_json("verify", "--data-dir", str(target))
    assert (code, document["ok"], document["error"]["code"]) == (1, False, "VERIFICATION_FAILED")
    assert document["violations"] == [
        {"stage": "CHECKSUM", "problem": "stray.txt is present but not listed in checksums.json"},
        {
            "stage": "REGENERATION",
            "problem": "stray.txt is not part of what seed 7, profile example generates",
        },
    ]
    assert err.splitlines()[0] == f"verify: 2 problems in {target}."
    assert document["error"]["message"] == f"verify: 2 problems in {target}."
    code, document, _ = run_json("generate", "--seed", "7", "--profile", "example", "--out", str(target))
    assert (code, document["error"]["code"]) == (1, "OUTPUT_DIRECTORY")


def test_counted_messages_use_the_singular_for_one(tmp_path: Path) -> None:
    target = tmp_path / "data"
    _, out, _ = run("generate", "--seed", "7", "--profile", "example", "--out", str(target))
    assert "Invariants: all checks passed over 20 tape rows" in out
    checksums = target / "checksums.json"
    checksums.write_text(json.dumps(json.loads(checksums.read_text())))
    _, _, err = run("verify", "--data-dir", str(target))
    assert err.splitlines() == [
        f"verify: 1 problem in {target}.",
        "  checksums.json differs from what seed 7, profile example generates",
    ]
    (target / "a.txt").write_text("x")
    _, _, err = run("verify", "--data-dir", str(target))
    assert err.splitlines()[0] == f"verify: 3 problems in {target}."


AMOUNT_FORMS = (r"[0-9]+", r"\$[0-9]{1,3}(,[0-9]{3})*", r"[0-9]+\.[0-9]")


def amount_cells(data_dir: Path) -> list[str]:
    """The amount cell of every M_AMOUNT_ALT_FORMAT row, read with the csv module."""
    key = json.loads((data_dir / "answer_key" / "answer_key.json").read_text())
    cells = []
    for entry in (m for m in key["messiness"] if m["kind"] == "M_AMOUNT_ALT_FORMAT"):
        with (data_dir / entry["file"]).open(encoding="utf-8-sig", newline="") as handle:
            rows = [row for row in csv.reader(handle) if row][1:]
        cells.append(rows[entry["row_number"] - 1][4])
    return cells


def test_the_full_profile_uses_all_three_amount_forms_in_rotation(full_dir: Path) -> None:
    cells = amount_cells(full_dir)
    counts = [sum(1 for cell in cells if re.fullmatch(form, cell)) for form in AMOUNT_FORMS]
    assert len(cells) == 20 and counts == [7, 7, 6]
    key = json.loads((full_dir / "answer_key" / "answer_key.json").read_text())
    assert key["generator_version"] == "1.0.1"


def test_verify_refuses_amount_forms_out_of_rotation(small_dir: Path, tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(small_dir, copy)
    key = json.loads((copy / "answer_key" / "answer_key.json").read_text())
    entry = next(m for m in key["messiness"] if m["kind"] == "M_AMOUNT_ALT_FORMAT")
    tape = copy / entry["file"]
    with tape.open(encoding="utf-8-sig", newline="") as handle:
        cell = [row for row in csv.reader(handle) if row][entry["row_number"]][4]
    respelled = cell.replace("$", "").replace(",", "") if cell.startswith("$") else f"${int(cell):,}"
    tape.write_bytes(tape.read_bytes().replace(f'"{cell}"'.encode(), f'"{respelled}"'.encode(), 1))
    code, _, err = run("verify", "--data-dir", str(copy))
    assert code == 1
    assert "M_AMOUNT_ALT_FORMAT rows are not spread over the three forms in rotation" in err
