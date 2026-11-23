"""The contract checker passes on the repository and names what is wrong on a broken copy (SPEC 7.4)."""

from __future__ import annotations

import io
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from onepledge_contract_fakes.cli_output import CliOutput
from onepledge_contract_fakes.contracts_check.checker import ContractsChecker
from onepledge_contract_fakes.contracts_check.cli import main
from onepledge_contract_fakes.contracts_check.references import resolve

pytestmark = pytest.mark.contract


def copy_contracts(contracts_dir: Path, target: Path) -> Path:
    destination = target / "contracts"
    shutil.copytree(contracts_dir, destination, ignore=shutil.ignore_patterns("fakes"))
    return destination


def run(contracts_dir: Path, *options: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(["check", *options, "--contracts-dir", str(contracts_dir)], CliOutput(out, io.StringIO()))
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
    assert text.splitlines()[-1] == "contracts-check: FAILED, 1 problem"


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
    code = main(
        ["check", "--wide", "--contracts-dir", str(tmp_path / "absent")], CliOutput(io.StringIO(), err)
    )
    assert code == 1 and err.getvalue().count("\n") == 1


def run_json(contracts_dir: Path) -> tuple[int, dict[str, object], str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(["check", "--json", "--contracts-dir", str(contracts_dir)], CliOutput(out, err))
    document: dict[str, object] = json.loads(out.getvalue())
    return code, document, err.getvalue()


def test_a_broken_example_is_one_problem_and_its_vector_is_skipped(
    contracts_dir: Path, tmp_path: Path
) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    example = broken / "examples" / "registry" / "certificate.response.json"
    document = json.loads(example.read_text())
    del document["body"]
    example.write_text(json.dumps(document))
    code, text = run(broken, "--wide")
    assert code == 1
    assert (
        "contracts-check: SKIP vectors/signatures.json: not checked, depends on "
        "registry/certificate.response.json which is invalid"
    ) in text.splitlines()
    assert text.splitlines()[-1] == "contracts-check: FAILED, 1 problem"
    assert "Error" not in text and "(KeyError)" not in text


def test_no_output_names_a_python_class(contracts_dir: Path, tmp_path: Path) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    (broken / "examples" / "registry" / "outbox.response.json").write_text("{broken")
    (broken / "examples" / "vectors" / "tokenization.json").write_text("[1]")
    code, text = run(broken)
    assert code == 1
    assert "examples/registry/outbox.response.json: not valid JSON" in text
    assert "examples/vectors/tokenization.json: not a JSON object" in text
    second = copy_contracts(contracts_dir, tmp_path / "second")
    (second / "registry.yaml").write_bytes(b"\xff\xfe")
    (second / "key_holder.yaml").write_text("a: [unclosed")
    code, more = run(second)
    assert code == 1
    assert "registry.yaml: not UTF-8 text" in more and "key_holder.yaml: not valid YAML" in more
    for name in ("Error", "Exception", "KeyError", "TypeError", "ValueError"):
        assert name not in text + more


def test_json_output_is_one_document_on_success_and_on_failure(contracts_dir: Path, tmp_path: Path) -> None:
    code, document, err = run_json(contracts_dir)
    assert (code, document["ok"], document["files"], document["problems"], err) == (0, True, 4, 0, "")
    checks = document["checks"]
    assert isinstance(checks, list) and {c["status"] for c in checks} == {"OK"}
    broken = copy_contracts(contracts_dir, tmp_path)
    (broken / "examples" / "registry" / "outbox.response.json").write_text("{}")
    code, document, err = run_json(broken)
    assert (code, document["ok"]) == (1, False)
    assert document["error"] == {"code": "CONTRACTS_INVALID", "message": err.strip()}
    code, document, err = run_json(tmp_path / "absent")
    error = document["error"]
    assert isinstance(error, dict)
    assert (code, document["ok"], error["code"]) == (1, False, "CONTRACTS_DIR_NOT_FOUND")


def test_an_unexpected_failure_is_one_sentence_and_the_traceback_needs_debug(
    contracts_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self: ContractsChecker) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(ContractsChecker, "run", explode)
    out, err = io.StringIO(), io.StringIO()
    assert main(["check", "--contracts-dir", str(contracts_dir)], CliOutput(out, err)) == 1
    record, sentence = err.getvalue().splitlines()
    assert (out.getvalue(), sentence) == ("", "Unexpected error. Run with --debug for details.")
    # SPEC_DECISIONS 78: the last resort also writes one error record with the type and the frames.
    fields = json.loads(record)
    assert (fields["level"], fields["event"], fields["exception_type"]) == (
        "error",
        "unhandled_exception",
        "RuntimeError",
    )
    assert " explode" in fields["stack"] and "boom" not in record
    err = io.StringIO()
    assert (
        main(["check", "--debug", "--contracts-dir", str(contracts_dir)], CliOutput(io.StringIO(), err)) == 1
    )
    text = err.getvalue()
    # SPEC_DECISIONS 53.12: the type and the frames, never the message or a source line.
    text = "\n".join(line for line in text.splitlines() if not line.startswith("{")) + "\n"
    assert text.startswith("RuntimeError, raised at:\n") and "boom" not in text and "Traceback" not in text
    assert "test_contracts_check.py:" in text and " explode\n" in text
    assert 'raise RuntimeError("boom")' not in text


def edit_cleaning_cases(broken: Path, change: Callable[[dict[str, Any]], None]) -> None:
    path = broken / "examples" / "cleaning_cases.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_the_cleaning_table_is_checked(contracts_dir: Path) -> None:
    code, text = run(contracts_dir)
    assert code == 0
    line = next(line for line in text.splitlines() if "cleaning cases" in line)
    assert line.startswith("contracts-check: OK   cleaning cases: ") and line.endswith(
        "have the documented shape"
    )


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda d: d.update(cases=d["cases"][:119]), "holds 119 cases; at least 120 are required"),
        (lambda d: d["cases"][2]["expected"].update(action="ACCEPT"), "does not follow from its problems"),
        (lambda d: d["cases"][0]["expected"].update(canonical=None), "canonical must be null exactly"),
        (
            lambda d: d["cases"][3]["expected"]["problems"].append({"field": "vin", "problem": "LOOKS_ODD"}),
            "SPEC 10.4 problem",
        ),
        (lambda d: d["cases"][1].update(kind="LEASE"), "unknown tape kind"),
        (lambda d: d["cases"][1].update(as_of_date="2026-W01-1"), "as_of_date that is not YYYY-MM-DD"),
        (lambda d: d["cases"][2].pop("note"), "has keys"),
        (lambda d: d["cases"].append(d["cases"][0]), "is listed twice"),
        (lambda d: d.update(cases=[c for c in d["cases"] if c["field"] != "row"]), "no case for row"),
    ],
)
def test_a_malformed_cleaning_table_is_named(
    contracts_dir: Path, tmp_path: Path, change: Callable[[dict[str, Any]], None], fragment: str
) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    edit_cleaning_cases(broken, change)
    code, text = run(broken)
    assert code == 1
    assert fragment in text


def test_a_missing_cleaning_table_is_one_problem(contracts_dir: Path, tmp_path: Path) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    (broken / "examples" / "cleaning_cases.json").unlink()
    code, text = run(broken)
    assert code == 1 and "examples/cleaning_cases.json is missing" in text.splitlines()[-2]
    assert text.splitlines()[-1] == "contracts-check: FAILED, 1 problem"


# SPEC_DECISIONS 43.11 and 40: no Python repr in any failing output, and a FAILED check never
# carries its success sentence in `--json`.


def _delete_key(path: str, key: str) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        target = root / "examples" / path
        document = json.loads(target.read_text())
        del document[key]
        target.write_text(json.dumps(document))

    return change


def _set_key(path: str, key: str, value: object) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        target = root / "examples" / path
        document = json.loads(target.read_text())
        document[key] = value
        target.write_text(json.dumps(document))

    return change


def _write(path: str, content: bytes) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        (root / path).write_bytes(content)

    return change


def _remove(path: str) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        (root / path).unlink()

    return change


def _yaml_edit(name: str, old: str, new: str) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        target = root / name
        target.write_text(target.read_text().replace(old, new, 1))

    return change


BROKEN_VARIANTS: dict[str, Callable[[Path], None]] = {
    "required": _delete_key("registry/certificate.response.json", "body"),
    "required_in_request": _delete_key("registry/submission.pledge.request.json", "kind"),
    "wrong_type": _set_key("registry/outbox.response.json", "has_more", "yes"),
    "enum": _set_key("registry/submission.pledge.request.json", "kind", "NONSENSE"),
    "extra_property": _set_key("registry/outbox.response.json", "unexpected['x']", 1),
    "format": _set_key("outside_records/title.response.json", "lien_recorded_on", "2026-02-30"),
    "invalid_json": _write("examples/key_holder/evaluate.request.json", b"{not json"),
    "invalid_utf8": _write("examples/vectors/tokenization.json", b"\xff\xfe\x00"),
    "broken_yaml": _write("registry.yaml", b"openapi: [unclosed"),
    "invalid_openapi": _yaml_edit("key_holder.yaml", "paths:", "pathz:"),
    "missing_index": _remove("examples/index.json"),
    "empty_index": _write("examples/index.json", b"[]"),
    "missing_vector": _remove("examples/vectors/tokenization.json"),
    "wrong_vector": _write("examples/vectors/signatures.json", b'{"x": 1}'),
    "unindexed": _write("examples/registry/unindexed.json", b"x"),
    "missing_contract": _remove("key_holder.yaml"),
    "cleaning_table": _write("examples/cleaning_cases.json", b'{"cases": [{"id": "(x)"}]}'),
}


@pytest.mark.parametrize("variant", sorted(BROKEN_VARIANTS))
def test_no_failing_output_holds_a_python_repr(contracts_dir: Path, tmp_path: Path, variant: str) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    BROKEN_VARIANTS[variant](broken)
    out, err = io.StringIO(), io.StringIO()
    assert main(["check", "--contracts-dir", str(broken)], CliOutput(out, err)) == 1
    text = out.getvalue() + err.getvalue()
    assert "FAIL" in text
    for character in ("[", "'", "("):
        assert character not in text, (variant, [line for line in text.splitlines() if character in line])
    code, document, _ = run_json(broken)
    checks = document["checks"]
    assert code == 1 and isinstance(checks, list)
    for check in checks:
        assert isinstance(check, dict)
        if check["status"] == "FAILED":
            assert (
                check["summary"]
                == f"{len(check['problems'])} problem{'s' if len(check['problems']) != 1 else ''} found"
            )


def test_quiet_says_it_has_no_effect_where_no_log_is_written(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["check", "--help"])
    assert "no effect: this command writes no log lines" in " ".join(capsys.readouterr().out.split())


def test_an_empty_contracts_dir_is_a_usage_error_and_the_metavar_is_dir(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exited:
        main(["check", "--contracts-dir", ""])
    assert exited.value.code == 2
    assert "must name a directory, not be empty" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["check", "--help"])
    assert "--contracts-dir DIR" in capsys.readouterr().out


# SPEC_DECISIONS 74: every file is hostile input. Each failure is one problem naming the file.
DEEP_JSON = b"[" * 100_000
DEEP_YAML = b"openapi: " + b"[" * 100_000


def _index_entry_file(value: str) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        path = root / "examples" / "index.json"
        document = json.loads(path.read_text())
        document["examples"][0]["file"] = value
        path.write_text(json.dumps(document))

    return change


def _signatures_file(value: str) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        path = root / "examples" / "vectors" / "signatures.json"
        document = json.loads(path.read_text())
        document["certificate"]["file"] = value
        path.write_text(json.dumps(document))

    return change


def _symlink_out(root: Path) -> None:
    outside = root.parent / "outside.json"
    outside.write_text("{}")
    (root / "examples" / "registry" / "escape.json").symlink_to(outside)
    _index_entry_file("registry/escape.json")(root)


HOSTILE: dict[str, tuple[Callable[[Path], None], str]] = {
    "deep_signatures": (
        _write("examples/vectors/signatures.json", DEEP_JSON),
        "examples/vectors/signatures.json: nests too deeply",
    ),
    "deep_tokenization": (
        _write("examples/vectors/tokenization.json", DEEP_JSON),
        "examples/vectors/tokenization.json: nests too deeply",
    ),
    "deep_index": (_write("examples/index.json", DEEP_JSON), "examples/index.json: nests too deeply"),
    "deep_cleaning": (
        _write("examples/cleaning_cases.json", DEEP_JSON),
        "examples/cleaning_cases.json: nests too deeply",
    ),
    "deep_example": (
        _write("examples/key_holder/evaluate.request.json", DEEP_JSON),
        "examples/key_holder/evaluate.request.json: nests too deeply",
    ),
    "deep_yaml": (_write("registry.yaml", DEEP_YAML), "registry.yaml: nests too deeply"),
    "yaml_alias": (_write("registry.yaml", b"a: &x [1]\nb: *x\n"), "registry.yaml: uses a YAML alias"),
    "yaml_too_large": (
        _write("registry.yaml", b"#" * (5 * 1024 * 1024 + 1)),
        "registry.yaml: larger than 5 MiB",
    ),
    "duplicate_key": (
        _write("examples/index.json", b'{"examples": [], "examples": []}'),
        "examples/index.json: not valid JSON",
    ),
    "long_number": (
        _write("examples/index.json", b'{"n": ' + b"1" * 31 + b"}"),
        "examples/index.json: not valid JSON",
    ),
    "index_parent_path": (
        _index_entry_file("../../../../etc/passwd"),
        "examples/index.json names a path outside the directory.",
    ),
    "index_absolute_path": (
        _index_entry_file("/etc/passwd"),
        "examples/index.json names a path outside the directory.",
    ),
    "index_symlink_out": (_symlink_out, "examples/registry/escape.json is a link out of the directory"),
    "vector_parent_path": (
        _signatures_file("../../../etc/passwd"),
        "examples/vectors/signatures.json names a path outside the directory.",
    ),
}


@pytest.mark.parametrize("variant", sorted(HOSTILE))
def test_a_hostile_file_is_one_problem_that_names_it(
    contracts_dir: Path, tmp_path: Path, variant: str
) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    change, problem = HOSTILE[variant]
    change(broken)
    out, err = io.StringIO(), io.StringIO()
    assert main(["check", "--contracts-dir", str(broken)], CliOutput(out, err)) == 1
    lines = out.getvalue().splitlines()
    assert lines.count(f"    {problem}") == 1, out.getvalue()
    assert "Unexpected error" not in out.getvalue() + err.getvalue()


# SPEC_DECISIONS 111: every $ref is resolved by the checker itself, local references only, and the
# third party validator is contained; review 4 failure 3 and quality finding 1.
def _ref(name: str, old: str, new: str) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        target = root / name
        text = target.read_text()
        assert old in text, old
        target.write_text(text.replace(old, new, 1))

    return change


BROKEN_REFERENCES: dict[str, tuple[Callable[[Path], None], str]] = {
    "outside_title": (
        _ref(
            "outside_records.yaml",
            "'#/components/schemas/TitleRecord'",
            "'#/components/schemas/TitleRecordX'",
        ),
        "outside_records.yaml",
    ),
    "key_holder_element": (
        _ref("key_holder.yaml", "'#/components/schemas/Element'", "'#/components/schemas/Elemen'"),
        "key_holder.yaml",
    ),
    "registry_token": (
        _ref("registry.yaml", "'#/components/schemas/Token'", "'#/components/schemas/Tokn'"),
        "registry.yaml",
    ),
    "registry_error_response": (
        _ref("registry.yaml", "'#/components/responses/Error'", "'#/components/responses/Erro'"),
        "registry.yaml",
    ),
    "submission_request_body": (
        _ref(
            "registry.yaml",
            "'#/components/schemas/SubmissionRequest'",
            "'#/components/schemas/SubmissionReques'",
        ),
        "registry.yaml",
    ),
    "other_file": (
        _ref("bank_agent.yaml", "'#/components/schemas/", "'other.yaml#/components/schemas/"),
        "bank_agent.yaml",
    ),
}


@pytest.mark.parametrize("variant", sorted(BROKEN_REFERENCES))
def test_a_reference_that_does_not_resolve_fails_naming_the_file(
    contracts_dir: Path, tmp_path: Path, variant: str
) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    change, name = BROKEN_REFERENCES[variant]
    change(broken)
    out, err = io.StringIO(), io.StringIO()
    code = main(["check", "--wide", "--contracts-dir", str(broken)], CliOutput(out, err))
    text = out.getvalue()
    assert code == 1, text
    assert "Unexpected error" not in text + err.getvalue()
    assert f"contracts-check: FAIL openapi {name}: " in text, text
    problems = [line.strip() for line in text.splitlines() if line.startswith("    ")]
    assert any(line.startswith(f"{name}: the reference at /") for line in problems), problems
    assert "contracts-check: SKIP " in text, text
    assert text.splitlines()[-1].startswith("contracts-check: FAILED, ")


def test_an_operation_pointing_at_the_wrong_schema_fails(contracts_dir: Path, tmp_path: Path) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    _ref("registry.yaml", "'#/components/schemas/SubmissionRequest'", "'#/components/schemas/Token'")(broken)
    out, err = io.StringIO(), io.StringIO()
    code = main(["check", "--wide", "--contracts-dir", str(broken)], CliOutput(out, err))
    lines = out.getvalue().splitlines()
    assert code == 1, lines
    assert "contracts-check: OK   openapi registry.yaml: valid OpenAPI 3.1" in lines
    failed = [line for line in lines if line.startswith("contracts-check: FAIL operations: ")]
    assert failed, lines
    assert any("submission.pledge.request.json (POST /v1/submissions request)" in line for line in lines)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("pointer", "expected"),
    [
        ("#/a/1/c", 2),
        ("#/x~1y/~0z", 3),
        ("#/a/2", None),
        ("#/a/-1", None),
        ("#/a/01", None),
        ("#/a/" + chr(0x661), None),
        ("#/missing", None),
        ("#/a/0/b/deeper", None),
    ],
)
def test_a_reference_resolves_only_to_an_existing_node(pointer: str, expected: object) -> None:
    document = {"a": [{"b": 1}, {"c": 2}], "x/y": {"~z": 3}}
    assert resolve(document, pointer) == expected


# SPEC_DECISIONS 110: every path opened, named in a file or fixed by convention, is a regular file
# (or a real directory) inside the directory, with no link on the way; review 4 quality 6 and 7.
def _link_out(relative: str, directory: bool = False) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        target = root / relative
        outside = root.parent / ("outside_dir" if directory else "outside.json")
        if directory:
            shutil.copytree(target, outside)
            shutil.rmtree(target)
        else:
            outside.write_bytes(target.read_bytes())
            target.unlink()
        target.symlink_to(outside)

    return change


def _link_inside(relative: str, to: str) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        target = root / relative
        (root / to).write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(root / to)

    return change


def _fifo(relative: str) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        (root / relative).unlink()
        os.mkfifo(root / relative)

    return change


def _directory(relative: str) -> Callable[[Path], None]:
    def change(root: Path) -> None:
        (root / relative).unlink()
        (root / relative).mkdir()

    return change


NOT_REGULAR: dict[str, tuple[Callable[[Path], None], str]] = {
    "index_link_out": (
        _link_out("examples/index.json"),
        "examples/index.json is a link out of the directory",
    ),
    "vectors_link_out": (
        _link_out("examples/vectors", directory=True),
        "examples/vectors is a link out of the directory",
    ),
    "yaml_link_out": (_link_out("registry.yaml"), "registry.yaml is a link out of the directory"),
    "cleaning_link_inside": (
        _link_inside("examples/cleaning_cases.json", "examples/copy.json"),
        "examples/cleaning_cases.json is not a regular file",
    ),
    "yaml_fifo": (_fifo("registry.yaml"), "registry.yaml is not a regular file"),
    "index_fifo": (_fifo("examples/index.json"), "examples/index.json is not a regular file"),
    "example_fifo": (
        _fifo("examples/key_holder/healthz.response.json"),
        "examples/key_holder/healthz.response.json is not a regular file",
    ),
    "index_directory": (
        _directory("examples/index.json"),
        "examples/index.json is a directory where a file is expected",
    ),
    "yaml_directory": (
        _directory("key_holder.yaml"),
        "key_holder.yaml is a directory where a file is expected",
    ),
    "index_too_large": (
        _write("examples/index.json", b" " * (5 * 1024 * 1024 + 1)),
        "examples/index.json: larger than 5 MiB",
    ),
}


@pytest.mark.timeout(30)
@pytest.mark.parametrize("variant", sorted(NOT_REGULAR))
def test_a_path_that_is_not_a_regular_file_inside_is_refused_by_name(
    contracts_dir: Path, tmp_path: Path, variant: str
) -> None:
    broken = copy_contracts(contracts_dir, tmp_path)
    change, problem = NOT_REGULAR[variant]
    change(broken)
    out, err = io.StringIO(), io.StringIO()
    code = main(["check", "--wide", "--contracts-dir", str(broken)], CliOutput(out, err))
    text = out.getvalue()
    assert code == 1, text
    assert f"    {problem}" in text.splitlines(), text
    assert "Unexpected error" not in text + err.getvalue()
    assert text.splitlines()[-1].startswith("contracts-check: FAILED, ")
