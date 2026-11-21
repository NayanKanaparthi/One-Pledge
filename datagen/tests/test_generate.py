"""Generated data sets: determinism, sizes, formats, labels and the committed example (SPEC 5)."""

from __future__ import annotations

import collections
import csv
import filecmp
import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from onepledge_datagen.application.profiles import CASE_COUNTS
from onepledge_datagen.domain import catalog
from onepledge_datagen.domain.entities import Profile

pytestmark = pytest.mark.integration

Generator = Callable[[Path, Profile, int], Path]
SEED = 20261117


def load(directory: Path, name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((directory / name).read_text())
    return document


def files_of(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def case_counts(directory: Path) -> collections.Counter[str]:
    return collections.Counter(c["case_type"] for c in load(directory, "answer_key/answer_key.json")["cases"])


def records(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    return [row for row in csv.reader(io.StringIO(text, newline="")) if row]


def test_the_same_seed_gives_identical_bytes(small_dir: Path, generator: Generator, tmp_path: Path) -> None:
    again = generator(tmp_path / "again", Profile.SMALL, SEED)
    assert files_of(again) == files_of(small_dir)


def test_another_seed_gives_other_data_with_the_same_counts(
    small_dir: Path, generator: Generator, tmp_path: Path
) -> None:
    other = generator(tmp_path / "other", Profile.SMALL, 4242)
    first, second = files_of(small_dir), files_of(other)
    assert (
        first["tapes/HARBOR/S01_HARBOR-SUMMIT_BACK_BOOK.csv"]
        != second["tapes/HARBOR/S01_HARBOR-SUMMIT_BACK_BOOK.csv"]
    )
    assert case_counts(small_dir) == case_counts(other)


@pytest.mark.parametrize(("profile", "column"), [(Profile.FULL, 0), (Profile.SMALL, 1)])
def test_case_counts_equal_spec_5_7(profile: Profile, column: int, request: pytest.FixtureRequest) -> None:
    directory: Path = request.getfixturevalue("full_dir" if profile == Profile.FULL else "small_dir")
    counts = collections.Counter(
        c["case_type"] for c in load(directory, "answer_key/answer_key.json")["cases"]
    )
    assert dict(counts) == {case_type: sizes[column] for case_type, sizes in CASE_COUNTS.items()}


def test_full_profile_sizes_and_messiness(full_dir: Path) -> None:
    key = load(full_dir, "answer_key/answer_key.json")
    kinds = collections.Counter(m["kind"] for m in key["messiness"])
    fixable = ("M_VIN_LOWERCASE", "M_VIN_SEPARATORS", "M_VIN_OIQ", "M_WHITESPACE", "M_DATE_ALT_FORMAT",
               "M_AMOUNT_ALT_FORMAT", "M_BORROWER_LOWERCASE")  # fmt: skip
    assert all(kinds[kind] >= 10 for kind in fixable)
    unfixable = [kind for kind in kinds if kind not in fixable and kind != "M_TOTALS_ROW"]
    assert len(unfixable) == 10 and all(kinds[kind] >= 3 for kind in unfixable)
    assert sum(kinds[kind] for kind in unfixable) == 48
    vins: set[str] = set()
    for step in load(full_dir, "manifest.json")["steps"]:
        vins.update(
            row[1].upper().replace("-", "").replace(" ", "") for row in records(full_dir / step["file"])[1:]
        )
    assert 4_900 <= len(vins - {""}) <= 5_200


def test_manifest_and_reference_match_spec(small_dir: Path) -> None:
    manifest = load(small_dir, "manifest.json")
    expected = [
        (s.step_id, s.bank_code, s.facility_code, s.kind.value, s.as_of.isoformat(), s.concurrent_group)
        for s in catalog.STEPS
    ]
    steps = [
        (s["step_id"], s["bank_code"], s["facility_code"], s["kind"], s["as_of_date"], s["concurrent_group"])
        for s in manifest["steps"]
    ]
    assert steps == expected and manifest["timeline_end"] == "2026-11-16"
    for step in manifest["steps"]:
        assert len(records(small_dir / step["file"])) - 1 == step["data_rows"]
    reference = load(small_dir, "reference.json")
    assert [b["bank_code"] for b in reference["banks"]] == ["HARBOR", "NORTHGATE", "CALDER"]
    assert len(reference["facilities"]) == 5 and len(reference["demo_users"]) == 9
    assert {u["email"] for u in reference["demo_users"]} >= {"officer@calder.example", "admin@harbor.example"}
    assert "password" not in json.dumps(reference).lower()


def test_tapes_look_like_lender_files(small_dir: Path) -> None:
    summit = (small_dir / "tapes/HARBOR/S01_HARBOR-SUMMIT_BACK_BOOK.csv").read_bytes()
    lakeside = (small_dir / "tapes/HARBOR/S02_HARBOR-LAKESIDE_BACK_BOOK.csv").read_bytes()
    assert summit.startswith(
        b"\xef\xbb\xbfLoan #,VIN,Cust ID,Orig Date,Orig Amt,Curr Bal,APR,Term,Year,Make,Model,State\r\n"
    )
    assert summit.endswith(b"\r\n\r\n") and b"\n" not in summit.replace(b"\r\n", b"")
    last = summit.decode("utf-8-sig").split("\r\n")[-3]
    assert last.startswith('TOTAL,,,,,"$') and last.endswith('",,,,,,')
    first_row = summit.decode("utf-8-sig").split("\r\n")[1].split(",")
    assert first_row[3].count("/") == 2 and first_row[4].startswith('"$')
    assert not lakeside.startswith(b"\xef\xbb\xbf") and b"\r" not in lakeside
    assert lakeside.startswith(
        b"loan_id,vehicle_vin,borrower_id,origination_date,original_amount,current_balance,"
    )
    release = records(small_dir / "tapes/HARBOR/S11_HARBOR-SUMMIT_RELEASE.csv")[1:]
    assert release and all(row[0] and row[1] and not any(row[2:]) for row in release)


def test_no_file_outside_the_answer_key_carries_a_label(small_dir: Path) -> None:
    pattern_words = (
        "fraud",
        "copy",
        "invent",
        "planted",
        "lookalike",
        "blind_spot",
        "race_pair",
        "messi",
        "exact",
        "likely",
    )
    for path, data in files_of(small_dir).items():
        if path.startswith(("tapes/", "outside_records/")) or path in ("manifest.json", "reference.json"):
            text = data.decode("utf-8").lower()
            assert not [word for word in pattern_words if word in text], path
    names = [p for p in files_of(small_dir) if "answer" in p.lower()]
    assert names == ["answer_key/answer_key.json"]


def test_checksums_list_every_other_file(small_dir: Path) -> None:
    listed = load(small_dir, "checksums.json")["files"]
    everything = files_of(small_dir)
    assert set(listed) == set(everything) - {"checksums.json"}
    assert all(hashlib.sha256(everything[path]).hexdigest() == digest for path, digest in listed.items())


@pytest.mark.contract
def test_the_example_profile_equals_the_committed_contract_files(example_dir: Path, repository: Path) -> None:
    committed = repository / "contracts" / "examples" / "tapes"
    generated = example_dir / "contract_example"
    comparison = filecmp.dircmp(generated, committed)
    assert not comparison.left_only and not comparison.right_only
    for path in sorted(p for p in generated.rglob("*") if p.is_file()):
        assert path.read_bytes() == (committed / path.relative_to(generated)).read_bytes(), path.name
    tape = (example_dir / "tapes/HARBOR/S01_HARBOR-SUMMIT_PLEDGE.csv").read_bytes()
    assert tape == (committed / "example_tape_20.csv").read_bytes()


@pytest.mark.contract
def test_the_example_has_the_composition_of_spec_5_6(repository: Path) -> None:
    expected = json.loads((repository / "contracts/examples/tapes/example_tape_20.expected.json").read_text())
    rows = expected["rows"]
    assert len(rows) == 20 and len(records(repository / "contracts/examples/tapes/example_tape_20.csv")) == 21
    problems = collections.Counter(i["problem"] for r in rows for i in r["cleaning_issues"])
    assert problems == {"VIN_CASE_NORMALISED": 1, "VIN_SEPARATORS_REMOVED": 1, "DATE_FORMAT_NORMALISED": 1,
                        "VIN_CHECK_DIGIT_INVALID": 1, "AMOUNT_UNPARSEABLE": 1}  # fmt: skip
    held = [r for r in rows if r["local_state"] == "HELD_LOCAL"]
    failed = sorted(sorted(c["check_type"] for c in r["checks"] if c["result"] == "FAIL") for r in held)
    assert failed == [["LIENHOLDER_MATCH"], ["PAYMENTS_RECEIVED"], ["PAYMENTS_RECEIVED", "TITLE_EXISTS"]]
    assert sum(r["local_state"] == "PENDING" for r in rows) == 15
    assert sum(r["disposition"] == "REJECTED" for r in rows) == 2
    assert all(len(r["checks"]) == 4 for r in rows if r["disposition"] == "ACCEPTED_NEW")
