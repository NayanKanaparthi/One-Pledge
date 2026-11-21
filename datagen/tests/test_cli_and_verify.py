"""The command line: generate, verify, refusals and one sentence errors (SPEC 5.1, 19)."""

from __future__ import annotations

import io
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from onepledge_datagen.interface.cli import main
from onepledge_datagen.interface.output import Console

pytestmark = pytest.mark.integration


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), Console(out, err))
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
    assert "invalid choice: 'nope'" in capsys.readouterr().err


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
    assert "(baseline): outcome HELD_LOCAL instead of LOCKED" in err


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
    assert code == 1 and err == "manifest.json is not valid JSON.\n"


def test_verify_checks_the_example_contract_files(example_dir: Path, tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(example_dir, copy)
    expected = copy / "contract_example" / "example_tape_20.expected.json"
    expected.write_text(expected.read_text().replace("PENDING", "HELD_LOCAL", 1))
    code, _, err = run("verify", "--data-dir", str(copy))
    assert code == 1 and "differs from the oracle's results" in err


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
        "answer key inherent_fixes of HARBOR-LAKESIDE is [], its lender format causes "
        "['BORROWER_REF_NORMALISED']" in err
    )
    assert "(baseline): cleaning problems ['BORROWER_REF_NORMALISED'] instead of []" in err


def test_verify_refuses_an_inherent_fix_the_format_does_not_cause(small_dir: Path, tmp_path: Path) -> None:
    def add(key: dict[str, Any]) -> None:
        key["inherent_fixes"]["HARBOR-SUMMIT"] = ["WHITESPACE_TRIMMED"]

    code, _, err = run("verify", "--data-dir", str(tampered_key(small_dir, tmp_path / "copy", add)))
    assert code == 1
    assert "inherent_fixes of HARBOR-SUMMIT is ['WHITESPACE_TRIMMED'], its lender format causes []" in err


def test_verify_refuses_a_key_that_omits_a_facility(small_dir: Path, tmp_path: Path) -> None:
    def omit(key: dict[str, Any]) -> None:
        del key["inherent_fixes"]["CALDER-SUMMIT"]

    code, _, err = run("verify", "--data-dir", str(tampered_key(small_dir, tmp_path / "copy", omit)))
    assert code == 1
    assert "reference.json has ['CALDER-SUMMIT', 'HARBOR-LAKESIDE'" in err


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
