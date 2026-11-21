"""The contract checker passes on the repository and names what is wrong on a broken copy (SPEC 7.4)."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

from onepledge_contract_fakes.cli_output import CliOutput
from onepledge_contract_fakes.contracts_check.checker import ContractsChecker
from onepledge_contract_fakes.contracts_check.cli import main

pytestmark = pytest.mark.contract


def copy_contracts(contracts_dir: Path, target: Path) -> Path:
    destination = target / "contracts"
    shutil.copytree(contracts_dir, destination, ignore=shutil.ignore_patterns("fakes"))
    return destination


def run(contracts_dir: Path) -> tuple[int, str]:
    out = io.StringIO()
    code = main(["check", "--contracts-dir", str(contracts_dir)], CliOutput(out, io.StringIO()))
    return code, out.getvalue()


def test_the_repository_contracts_pass(contracts_dir: Path) -> None:
    code, text = run(contracts_dir)
    assert code == 0, text
    lines = text.splitlines()
    assert lines[-1].startswith("contracts-check: OK (4 files, ")
    assert sum(line.startswith("contracts-check: OK   openapi ") for line in lines) == 4


def test_a_missing_required_field_fails_and_names_the_file(contracts_dir: Path, tmp_path: Path) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    example = broken / "examples" / "registry" / "outbox.response.json"
    document = json.loads(example.read_text())
    del document["has_more"]
    example.write_text(json.dumps(document))
    code, text = run(broken)
    assert code == 1
    assert "examples/registry/outbox.response.json" in text and "has_more" in text
    assert "contracts-check: FAIL examples: 1 problem" in text.splitlines()
    assert text.splitlines()[-1] == "contracts-check: FAILED (1 problem)"


def test_a_wrong_format_fails(contracts_dir: Path, tmp_path: Path) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    example = broken / "examples" / "outside_records" / "title.response.json"
    document = json.loads(example.read_text())
    document["lien_recorded_on"] = "2026-02-30"
    example.write_text(json.dumps(document))
    code, text = run(broken)
    assert code == 1 and "title.response.json" in text


def test_an_unindexed_example_and_a_dangling_reference_fail(contracts_dir: Path, tmp_path: Path) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    (broken / "examples" / "key_holder" / "extra.json").write_text("{}")
    (broken / "examples" / "key_holder" / "healthz.response.json").unlink()
    report = ContractsChecker(broken).run()
    problems = [p for outcome in report.outcomes for p in outcome.problems]
    assert any("examples/key_holder/extra.json is not listed" in p for p in problems)
    assert any("externalValue examples/key_holder/healthz.response.json" in p for p in problems)
    assert any("lists examples/key_holder/healthz.response.json, which does not exist" in p for p in problems)


def test_an_altered_vector_fails(contracts_dir: Path, tmp_path: Path) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    path = broken / "examples" / "vectors" / "tokenization.json"
    vector = json.loads(path.read_text())
    vector["tokens"][3]["token"] = "0" * 64
    path.write_text(json.dumps(vector))
    signatures_path = broken / "examples" / "vectors" / "signatures.json"
    signatures = json.loads(signatures_path.read_text())
    signatures["checkpoint"]["signature"] = signatures["certificate"]["signature"]
    signatures_path.write_text(json.dumps(signatures))
    code, text = run(broken)
    assert code == 1
    assert "AMOUNT_EXACT: token differs" in text
    assert "checkpoint_latest.response.json: signature differs" in text


def test_an_invalid_openapi_document_fails(contracts_dir: Path, tmp_path: Path) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    path = broken / "key_holder.yaml"
    path.write_text(path.read_text().replace("openapi: 3.1.0", "openapi: 3.0.0").replace("paths:", "pathz:"))
    code, text = run(broken)
    assert code == 1 and "key_holder.yaml" in text


def test_a_missing_directory_is_one_sentence(tmp_path: Path) -> None:
    err = io.StringIO()
    code = main(["check", "--contracts-dir", str(tmp_path / "absent")], CliOutput(io.StringIO(), err))
    assert code == 1 and err.getvalue().count("\n") == 1
