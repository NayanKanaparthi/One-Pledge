"""`verify` proves conformance, not only consistency (SPEC_DECISIONS 43.2, 43.3, 43.11, 43.12).

Every tampering the review listed, with the checksums repaired, still exits 0 under the
invariants alone; the second stage regenerates the data set from the manifest's seed and
profile and names each file that differs. No failing output holds a Python repr, and the
ordinary mistakes of an operator get their own sentence.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from onepledge_datagen import GENERATOR_VERSION
from onepledge_datagen.interface.cli import main
from onepledge_datagen.interface.output import Console

pytestmark = pytest.mark.integration

Change = Callable[[Path], object]
ORIGIN = "seed 20261117, profile small"


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), Console(out, err))
    return code, out.getvalue(), err.getvalue()


def repair_checksums(directory: Path) -> None:
    path = directory / "checksums.json"
    document = json.loads(path.read_text())
    if not isinstance(document, dict):
        return
    document["files"] = {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file() and p.name != "checksums.json"
    }
    path.write_text(json.dumps(document, indent=2) + "\n")


def edit_json(relative: str, change: Callable[[Any], object]) -> Change:
    def apply(directory: Path) -> None:
        path = directory / relative
        document = json.loads(path.read_text())
        change(document)
        path.write_text(json.dumps(document, indent=2) + "\n")

    return apply


def tampered(source: Path, target: Path, change: Change, repair: bool = True) -> Path:
    shutil.copytree(source, target)
    change(target)
    if repair:
        repair_checksums(target)
    return target


MANIFEST, KEY, REFERENCE = "manifest.json", "answer_key/answer_key.json", "reference.json"
# The review's list of tamperings that kept `verify` at exit 0 (finding 2), each with the file it changes.
CONSISTENT_TAMPERINGS: dict[str, tuple[Change, str]] = {
    "step kind": (edit_json(MANIFEST, lambda m: m["steps"][0].__setitem__("kind", "NONSENSE")), MANIFEST),
    "concurrent group removed": (
        edit_json(MANIFEST, lambda m: [s.__setitem__("concurrent_group", None) for s in m["steps"]]),
        MANIFEST,
    ),
    "timeline end": (edit_json(MANIFEST, lambda m: m.__setitem__("timeline_end", "2020-01-01")), MANIFEST),
    "blind spot case dropped": (
        edit_json(
            KEY, lambda k: k.__setitem__("cases", [c for c in k["cases"] if c["category"] != "BLIND_SPOT"])
        ),
        KEY,
    ),
    "category swapped": (
        edit_json(KEY, lambda k: k["cases"][0].__setitem__("category", "HONEST_LOOKALIKE")),
        KEY,
    ),
    "kinds matched emptied": (
        edit_json(
            KEY,
            lambda k: [
                r["expected"].__setitem__("kinds_matched_include", []) for c in k["cases"] for r in c["rows"]
            ],
        ),
        KEY,
    ),
    "failed check reasons emptied": (
        edit_json(
            KEY,
            lambda k: [
                r["expected"].__setitem__("failed_check_reasons", {}) for c in k["cases"] for r in c["rows"]
            ],
        ),
        KEY,
    ),
    "baseline removed": (edit_json(KEY, lambda k: k.pop("baseline")), KEY),
    "race group nulled": (
        edit_json(KEY, lambda k: [c.__setitem__("race_group", None) for c in k["cases"]]),
        KEY,
    ),
    "bank removed": (edit_json(REFERENCE, lambda r: r["banks"].pop(0)), REFERENCE),
    "demo users removed": (edit_json(REFERENCE, lambda r: r.pop("demo_users")), REFERENCE),
    "lender LEI changed": (
        edit_json(REFERENCE, lambda r: r["lenders"][0].__setitem__("lei", "X")),
        REFERENCE,
    ),
    "profile changed": (edit_json(MANIFEST, lambda m: m.__setitem__("profile", "full")), "every file"),
}


@pytest.mark.parametrize("name", sorted(CONSISTENT_TAMPERINGS))
def test_a_consistent_tampering_differs_from_the_regeneration(
    small_dir: Path, tmp_path: Path, name: str
) -> None:
    change, changed = CONSISTENT_TAMPERINGS[name]
    code, _, err = run("verify", "--data-dir", str(tampered(small_dir, tmp_path / "copy", change)))
    assert code == 1, err
    if changed == "every file":
        assert f"{MANIFEST} differs from what seed 20261117, profile full generates" in err
    else:
        assert f"  {changed} differs from what {ORIGIN} generates\n" in err


def test_an_untouched_data_set_is_byte_identical_to_its_regeneration(
    small_dir: Path, example_dir: Path
) -> None:
    for directory in (small_dir, example_dir):
        code, out, err = run("verify", "--data-dir", str(directory))
        assert (code, err) == (0, "")
        assert out.endswith("every invariant holds, byte identical to its regeneration)\n")


def test_an_extra_file_is_named(small_dir: Path, tmp_path: Path) -> None:
    def change(directory: Path) -> None:
        (directory / "extra.json").write_text("{}")

    code, _, err = run("verify", "--data-dir", str(tampered(small_dir, tmp_path / "copy", change)))
    assert code == 1
    assert f"  extra.json is not part of what {ORIGIN} generates\n" in err


def test_a_missing_file_is_one_sentence_naming_it(small_dir: Path, tmp_path: Path) -> None:
    def change(directory: Path) -> None:
        (directory / "outside_records" / "contracts.csv").unlink()

    code, _, err = run("verify", "--data-dir", str(tampered(small_dir, tmp_path / "copy", change)))
    assert (code, err) == (1, "outside_records/contracts.csv is missing.\n")


@pytest.mark.parametrize("version", ["0.0.1", "9.9.9", "x'[(", 5])
def test_another_generator_version_is_one_sentence(small_dir: Path, tmp_path: Path, version: object) -> None:
    change = edit_json(MANIFEST, lambda m: m.__setitem__("generator_version", version))
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["verify", "--data-dir", str(tampered(small_dir, tmp_path / "copy", change))], Console(out, err)
    )
    shown = version if version in ("0.0.1", "9.9.9") else "another"
    assert (code, out.getvalue()) == (1, "")
    assert err.getvalue() == (
        f"manifest.json was written by generator version {shown} and this is version {GENERATOR_VERSION}, "
        "which cannot regenerate it; verify it with the generator that wrote it.\n"
    )


@pytest.mark.parametrize(
    ("change", "sentence"),
    [
        (
            lambda m: m.pop("profile"),
            "manifest.json has no valid profile: one of full, small, example is required",
        ),
        (lambda m: m.__setitem__("profile", "nope"), "manifest.json has no valid profile"),
        (lambda m: m.__setitem__("seed", -1), "manifest.json has no valid seed"),
        (lambda m: m.__setitem__("seed", 10**18), "manifest.json has no valid seed"),
    ],
)
def test_a_manifest_without_a_valid_seed_or_profile_says_so(
    small_dir: Path, tmp_path: Path, change: Callable[[Any], None], sentence: str
) -> None:
    code, _, err = run(
        "verify", "--data-dir", str(tampered(small_dir, tmp_path / "copy", edit_json(MANIFEST, change)))
    )
    assert code == 1 and f"  {sentence}" in err


def raw(relative: str, change: Callable[[bytes], bytes]) -> Change:
    def apply(directory: Path) -> None:
        path = directory / relative
        path.write_bytes(change(path.read_bytes()))

    return apply


def first_cell_replaced(relative: str, column: int, value: str) -> Change:
    def apply(directory: Path) -> None:
        path = directory / relative
        lines = path.read_text().splitlines(keepends=True)
        cells = lines[1].rstrip("\n").split(",")
        cells[column] = value
        lines[1] = ",".join(cells) + "\n"
        path.write_text("".join(lines))

    return apply


# SPEC_DECISIONS 43.3: a malformed data set file names the file and the problem.
@pytest.mark.parametrize(
    ("change", "sentence"),
    [
        (raw(MANIFEST, lambda b: b"{"), "manifest.json is not valid JSON at line 1, column 2.\n"),
        (raw(MANIFEST, lambda b: b'{"seed": ' + b"9" * 5000 + b"}"), "manifest.json nests too deeply"),
        (raw(MANIFEST, lambda b: b"[" * 100_000), "manifest.json nests too deeply"),
        (raw(MANIFEST, lambda b: b'{"seed": 1, "seed": 2}'), "manifest.json repeats a key or holds NaN"),
        (raw(KEY, lambda b: b"\xff\xfe"), "answer_key/answer_key.json is not UTF-8 text.\n"),
        (
            first_cell_replaced("outside_records/payments.csv", 3, "²"),
            "payments.csv line 2 column amount_cents is not a positive whole number of cents.\n",
        ),
        (
            first_cell_replaced("outside_records/payments.csv", 2, "٢٠٢٦-01-01"),
            "payments.csv line 2 column paid_on is not a YYYY-MM-DD date.\n",
        ),
        (
            first_cell_replaced("outside_records/contracts.csv", 3, "1.5"),
            "contracts.csv line 2 column amount_cents is not a positive whole number of cents.\n",
        ),
        (raw("outside_records/titles.csv", lambda b: b + b"X,Y\n"), "titles.csv line"),
        (
            edit_json(MANIFEST, lambda m: m["steps"][0].__setitem__("as_of_date", "2026-13-45")),
            "manifest.json step: field as_of_date is not a YYYY-MM-DD date.\n",
        ),
    ],
)
def test_a_malformed_file_is_one_sentence_naming_it(
    small_dir: Path, tmp_path: Path, change: Change, sentence: str
) -> None:
    code, out, err = run(
        "verify", "--data-dir", str(tampered(small_dir, tmp_path / "copy", change, repair=False))
    )
    assert (code, out) == (1, "")
    assert sentence in err and err.count("\n") == 1 and "Unexpected" not in err


# SPEC_DECISIONS 43.11: no failing output of verify holds a Python repr.
REPR_VARIANTS: dict[str, Change] = {
    **{name: change for name, (change, _) in CONSISTENT_TAMPERINGS.items()},
    "checksums files 5": raw("checksums.json", lambda b: b'{"files": 5}'),
    "checksums list": raw("checksums.json", lambda b: b"[]"),
    "stray file": lambda d: (d / "stray.txt").write_text("x"),
    "inherent fixes dropped": edit_json(
        KEY, lambda k: k["inherent_fixes"].__setitem__("HARBOR-LAKESIDE", [])
    ),
    "inherent fixes added": edit_json(KEY, lambda k: k["inherent_fixes"].__setitem__("HARBOR-SUMMIT", ["X"])),
    "facility omitted": edit_json(KEY, lambda k: k["inherent_fixes"].pop("CALDER-SUMMIT")),
    "outcome flipped": edit_json(
        KEY, lambda k: k["cases"][0]["rows"][-1]["expected"].__setitem__("outcome", "LOCKED")
    ),
    "failed checks": edit_json(
        KEY, lambda k: k["cases"][0]["rows"][0]["expected"].__setitem__("failed_checks", ["X"])
    ),
    "alert": edit_json(
        KEY, lambda k: k["cases"][0]["rows"][0]["expected"].__setitem__("alert", ["HOLDER", "EXACT"])
    ),
    "titles row dropped": raw(
        "outside_records/titles.csv",
        lambda b: b"".join(b.splitlines(keepends=True)[:1] + b.splitlines(keepends=True)[2:]),
    ),
    "label word": raw("reference.json", lambda b: b.replace(b'"legal_name": "', b'"legal_name": "FRAUD ', 1)),
}


@pytest.mark.parametrize("name", sorted(REPR_VARIANTS))
def test_no_failing_output_holds_a_python_repr(small_dir: Path, tmp_path: Path, name: str) -> None:
    for repair in (True, False):
        target = tampered(small_dir, tmp_path / f"copy-{repair}", REPR_VARIANTS[name], repair)
        code, out, err = run("verify", "--data-dir", str(target))
        text = (out + err).replace(str(target), "DIR")
        assert code == 1
        for character in ("[", "'", "("):
            assert character not in text, (name, [line for line in text.splitlines() if character in line])


# SPEC_DECISIONS 43.12 and 37: command line rough edges.
@pytest.mark.parametrize(
    "argv",
    [
        ["generate", "--seed", "1", "--profile", "example", "--out", ""],
        ["generate", "--seed", "1", "--profile", "example", "--out", "  "],
        ["verify", "--data-dir", ""],
        *(
            ["generate", "--seed", seed, "--profile", "example", "--out", "x"]
            for seed in ("²", "٣", "+5", "-1", "1e3", " 7", "9" * 19, "9" * 5000, "")
        ),
    ],
)
def test_empty_paths_and_malformed_seeds_are_usage_errors(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exited:
        main(argv)
    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert "must name a directory, not be empty" in err or "must be a whole number from 0 to" in err


@pytest.mark.parametrize("command", ["generate", "verify"])
def test_help_names_dir_and_says_quiet_has_no_effect(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        main([command, "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert " DIR" in text and "DATA_DIR" not in text and " OUT" not in text
    assert "--quiet no effect: this command writes no log lines" in text
