"""Architecture of the bank agent package: layers, boundaries, size limits and code hygiene.

SPEC 2.1, 2.2 and 21.6; BUILD_PLAN section 9. Every check reads the source with `ast`
(or as text where a rule is about text), so a violation names the file and line.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from onepledge_bank_agent.application.evidence_ports import EvidenceStores
from onepledge_bank_agent.application.ports import UnitOfWork
from onepledge_testtools.repository_rules import (
    markdown_files,
    markdown_problems,
    python_modules,
    size_problems,
    source_tree_problems,
)

pytestmark = pytest.mark.unit

PACKAGE = Path(__file__).resolve().parents[2]
OWN = "onepledge_bank_agent"
SOURCE = PACKAGE / "src" / OWN
MIGRATIONS = PACKAGE / "migrations"
TESTS = PACKAGE / "tests"
OTHER_PACKAGES = frozenset(
    {"onepledge_datagen", "onepledge_outside_records", "onepledge_contract_fakes", "onepledge_evaluation"}
)
TEST_IMPORTS_ALLOWED = frozenset({"onepledge_outside_records", "onepledge_contract_fakes"})
LAYERS = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    "infrastructure": {"domain", "application", "infrastructure"},
    "interface": {"domain", "application", "infrastructure", "interface"},
}
ONLY_IN = {
    "sqlalchemy": {"infrastructure"},
    "psycopg": {"infrastructure"},
    "alembic": {"infrastructure"},
    "httpx": {"infrastructure"},
    "structlog": {"infrastructure"},
    "pydantic": {"infrastructure", "interface"},
    "fastapi": {"interface"},
    "uvicorn": {"interface"},
}
MARKERS = re.compile(
    r"\b(" + "|".join(("TO" + "DO", "FIX" + "ME", "X" + "XX", "HA" + "CK", "T" + "BD")) + r")\b"
)
VIN_LITERAL = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
CASE_NAMES = re.compile(
    r"2026111[7]|S0[1-9]_|S1[0-5]_|EXACT_[C]OPY|COPY_[A]MOUNT|COPY_[D]ATE|COPY_[V]IN|INVENT[E]D|TWO_[C]ARS"
    r"|MOVED_[A]FTER|REFINANC[E]D|RACE_[P]AIR|BLIND_[S]POT|M_[V]IN|M_[D]ATE|M_[A]MOUNT"
)
EM_DASH = chr(0x2014)
BUILTIN_ERRORS = frozenset(
    {"Exception", "ValueError", "RuntimeError", "TypeError", "KeyError", "LookupError"}
)


def source_files() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def layer(path: Path) -> str:
    parts = path.relative_to(SOURCE).parts
    return parts[0] if len(parts) > 1 else "package"


def imported(path: Path) -> Iterator[tuple[str, int]]:
    """Every absolute module name the file imports, with its line."""
    for node in ast.walk(parsed(path)):
        if isinstance(node, ast.Import):
            yield from ((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module, node.lineno


def where(path: Path, line: int) -> str:
    return f"{path.relative_to(PACKAGE)}:{line}"


def test_each_layer_imports_only_itself_and_the_layers_below() -> None:
    violations = []
    for path in source_files():
        allowed = LAYERS.get(layer(path), set(LAYERS))
        for name, line in imported(path):
            parts = name.split(".")
            if parts[0] == OWN and len(parts) > 1 and parts[1] in LAYERS and parts[1] not in allowed:
                violations.append(f"{where(path, line)} imports {name}")
    assert violations == []


def test_the_domain_and_application_import_only_the_standard_library() -> None:
    violations = [
        f"{where(path, line)} imports {name}"
        for path in source_files()
        if layer(path) in ("domain", "application")
        for name, line in imported(path)
        if name.split(".")[0] not in sys.stdlib_module_names and name.split(".")[0] != OWN
    ]
    assert violations == []


def test_frameworks_appear_only_in_their_layer() -> None:
    violations = [
        f"{where(path, line)} imports {name}"
        for path in source_files()
        for name, line in imported(path)
        if name.split(".")[0] in ONLY_IN and layer(path) not in ONLY_IN[name.split(".")[0]]
    ]
    assert violations == []


def test_no_other_onepledge_package_is_imported_by_the_product() -> None:
    files = [*source_files(), *MIGRATIONS.rglob("*.py")]
    violations = [
        f"{where(path, line)} imports {name}"
        for path in files
        for name, line in imported(path)
        if name.split(".")[0] in OTHER_PACKAGES
    ]
    assert violations == []


def test_the_tests_import_only_the_allowed_other_packages() -> None:
    violations = [
        f"{where(path, line)} imports {name}"
        for path in TESTS.rglob("*.py")
        for name, line in imported(path)
        if name.split(".")[0] in OTHER_PACKAGES - TEST_IMPORTS_ALLOWED
    ]
    assert violations == []


def test_sql_text_is_written_only_in_infrastructure() -> None:
    statement = re.compile(r"\b(SELECT|INSERT INTO|UPDATE \w+ SET|DELETE FROM)\b", re.IGNORECASE)
    violations = [
        f"{path.relative_to(PACKAGE)}:{number}"
        for path in source_files()
        if layer(path) in ("domain", "application", "interface")
        for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if statement.search(text) and not text.lstrip().startswith(("#", '"""'))
    ]
    assert violations == []


def test_the_environment_is_read_only_in_the_settings_module() -> None:
    settings = SOURCE / "infrastructure" / "settings.py"
    violations = [
        where(path, node.lineno)
        for path in [*source_files(), *MIGRATIONS.rglob("*.py")]
        if path != settings
        for node in ast.walk(parsed(path))
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv", "putenv")
    ]
    assert violations == []


def test_time_and_ids_are_injected_into_the_domain_and_application() -> None:
    calls = {
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("date", "today"),
        ("uuid", "uuid4"),
        ("time", "time"),
    }
    violations = [
        where(path, node.lineno)
        for path in source_files()
        if layer(path) in ("domain", "application")
        for node in ast.walk(parsed(path))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and (node.value.id, node.attr) in calls
    ]
    assert violations == []


def test_the_source_tree_keeps_the_spec_2_2_size_limits() -> None:
    """400 lines per module, 60 per function, 15 public methods per class (SPEC_DECISIONS 71 rule 1),
    checked by the rules every package shares (`onepledge_testtools.repository_rules`)."""
    assert source_tree_problems(SOURCE) == []


def test_every_migration_function_stays_within_60_lines() -> None:
    """The module limit is for `src/`; a migration's DDL may be long, its functions may not."""
    problems = [
        problem
        for path in python_modules(MIGRATIONS)
        for problem in size_problems(path.relative_to(PACKAGE), path.read_text(encoding="utf-8"))
        if " function " in problem or " class " in problem
    ]
    assert problems == []


def test_every_markdown_file_of_the_package_renders_its_tables_whole() -> None:
    """Rows keep the header's cell count and nothing interrupts a table (SPEC_DECISIONS 71 rule 2)."""
    assert markdown_files(PACKAGE)
    assert markdown_problems(markdown_files(PACKAGE), PACKAGE.parent) == []


def test_every_module_and_public_class_and_function_is_documented() -> None:
    violations = []
    for path in [*source_files(), *MIGRATIONS.rglob("*.py")]:
        tree = parsed(path)
        if path.name != "__init__.py" and not ast.get_docstring(tree):
            violations.append(f"{path.relative_to(PACKAGE)} has no module docstring")
        definitions = [node for node in tree.body if isinstance(node, ast.ClassDef | ast.FunctionDef)]
        violations += [
            f"{where(path, node.lineno)} {node.name} has no docstring"
            for node in definitions
            if not node.name.startswith("_") and not ast.get_docstring(node)
        ]
    assert violations == []


def test_every_exception_class_is_named_error_and_none_is_a_bare_builtin() -> None:
    violations = []
    for path in source_files():
        for node in ast.walk(parsed(path)):
            if isinstance(node, ast.ClassDef):
                bases = {base.id for base in node.bases if isinstance(base, ast.Name)}
                is_error = bases & BUILTIN_ERRORS or any(name.endswith("Error") for name in bases)
                if is_error and not node.name.endswith("Error"):
                    violations.append(f"{where(path, node.lineno)} {node.name} does not end in Error")
            if layer(path) in ("domain", "application") and isinstance(node, ast.Raise):
                raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                if isinstance(raised, ast.Name) and raised.id in BUILTIN_ERRORS:
                    violations.append(f"{where(path, node.lineno)} raises {raised.id}")
    assert violations == []


def test_no_module_level_mutable_state_global_statement_or_print() -> None:
    violations = []
    for path in source_files():
        tree = parsed(path)
        for statement in tree.body:
            value = statement.value if isinstance(statement, ast.Assign | ast.AnnAssign) else None
            if isinstance(value, ast.List | ast.Dict | ast.Set) and not _is_dunder_all(statement):
                violations.append(f"{where(path, statement.lineno)} module level mutable value")
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                violations.append(f"{where(path, node.lineno)} global")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                violations.append(f"{where(path, node.lineno)} print")
    assert violations == []


def _is_dunder_all(node: ast.stmt) -> bool:
    return isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
    )


def test_no_swallowed_exception_and_no_placeholder_body() -> None:
    violations = []
    for path in source_files():
        for node in ast.walk(parsed(path)):
            if isinstance(node, ast.ExceptHandler) and all(isinstance(item, ast.Pass) for item in node.body):
                violations.append(f"{where(path, node.lineno)} swallows an exception")
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Name | ast.Call):
                raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                if isinstance(raised, ast.Name) and raised.id == "NotImplementedError":
                    violations.append(f"{where(path, node.lineno)} NotImplementedError")
    assert violations == []


def test_no_marker_em_dash_vin_literal_case_name_or_scoring_file_in_the_package() -> None:
    violations = []
    files = [p for p in PACKAGE.rglob("*") if p.suffix in {".py", ".md", ".toml", ".ini", ".mako", ".yaml"}]
    for path in files:
        if "__pycache__" in path.parts or ".egg-info" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(PACKAGE)
        if MARKERS.search(text):
            violations.append(f"{relative} has a work in progress marker")
        if EM_DASH in text:
            violations.append(f"{relative} has an em dash")
        if "answer" + "_key" in text:
            violations.append(f"{relative} names the answer key")
        if path.is_relative_to(SOURCE) and (VIN_LITERAL.search(text) or CASE_NAMES.search(text)):
            violations.append(f"{relative} holds a VIN literal or a data set case name")
    assert violations == []


def test_no_suppression_without_a_code_and_a_reason() -> None:
    markers = ("# no" + "qa", "# type: " + "ignore")
    shape = re.compile(r"(:|\[)\s*[A-Za-z0-9-]+\]?\s+-\s+\S")
    violations = [
        f"{path.relative_to(PACKAGE)}:{number}"
        for path in PACKAGE.rglob("*.py")
        for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for marker in markers
        if marker in text and not shape.match(text[text.index(marker) + len(marker) :])
    ]
    assert violations == []


PATCHING_CALLS = frozenset({"setattr", "delattr", "setitem", "delitem"})
PATCHING_MODULES = ("unittest.mock", "mock", "pytest_mock")


def patching(tree: ast.Module) -> Iterator[tuple[str, int]]:
    """Every place a test replaces an attribute of a module or class instead of injecting a collaborator."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in PATCHING_CALLS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "monkeypatch"
        ):
            yield f"monkeypatch.{node.func.attr}", node.lineno
        elif isinstance(node, ast.Import):
            yield from ((alias.name, node.lineno) for alias in node.names if alias.name in PATCHING_MODULES)
        elif isinstance(node, ast.ImportFrom) and node.module in PATCHING_MODULES:
            yield node.module, node.lineno


def test_no_test_patches_a_module_or_class_instead_of_injecting_a_collaborator() -> None:
    """SPEC_DECISIONS 42 and 53.10: collaborators are injected through constructors; only the
    process environment is set by tests (`monkeypatch.setenv` and `delenv`)."""
    violations = [
        f"{where(path, line)} uses {name}"
        for path in sorted(TESTS.rglob("*.py"))
        for name, line in patching(parsed(path))
    ]
    assert violations == []


def test_the_patching_rule_finds_each_planted_form() -> None:
    planted = ast.parse(
        "import unittest.mock\n"
        "from unittest.mock import patch\n"
        "monkeypatch.setattr(module, 'name', value)\n"
        "monkeypatch.setitem(table, 'key', value)\n"
        "monkeypatch.setenv('NAME', 'value')\n"
    )
    assert [name for name, _ in patching(planted)] == [
        "unittest.mock", "unittest.mock", "monkeypatch.setattr", "monkeypatch.setitem",
    ]  # fmt: skip


def _properties(cls: type) -> set[str]:
    return {name for name, value in vars(cls).items() if isinstance(value, property)}


def test_the_evidence_group_holds_exactly_the_insert_only_proof_stores() -> None:
    """SPEC_DECISIONS 67: `unit.evidence` is certificates, checkpoints, quarantine and runs, and no
    proof store sits outside it."""
    proof_stores = {"certificates", "checkpoints", "quarantine", "runs"}
    assert _properties(EvidenceStores) == proof_stores
    assert not proof_stores & _properties(UnitOfWork)
    assert {"alerts", "requests", "tapes", "loans"} <= _properties(UnitOfWork), "mutable stores stay outside"
