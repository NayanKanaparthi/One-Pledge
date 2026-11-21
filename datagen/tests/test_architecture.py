"""Import boundaries of the generator (SPEC 2.1, 21.6)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from onepledge_datagen.domain.errors import DataGenError
from onepledge_datagen.domain.oracle.cleaning import TapeFormatError

pytestmark = pytest.mark.unit

SOURCE = Path(__file__).resolve().parents[1] / "src" / "onepledge_datagen"
OTHER_PACKAGES = (
    "onepledge_bank_agent",
    "onepledge_contract_fakes",
    "onepledge_outside_records",
    "onepledge_evaluation",
)
LAYERS = ("domain", "application", "infrastructure", "interface")
ALLOWED_INTERNAL = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    "infrastructure": {"domain", "application", "infrastructure"},
    "interface": {"domain", "application", "infrastructure", "interface"},
}


def imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_generator_imports_no_other_onepledge_package_and_no_third_party_code() -> None:
    for path in SOURCE.rglob("*.py"):
        for name in imports(path):
            top = name.split(".")[0]
            assert top not in OTHER_PACKAGES, f"{path.name} imports {name}"
            assert top in sys.stdlib_module_names or top in ("onepledge_datagen", "__future__"), (
                f"{path.name}: {name}"
            )


def test_layers_only_import_inward() -> None:
    for layer in LAYERS:
        for path in (SOURCE / layer).rglob("*.py"):
            for name in imports(path):
                parts = name.split(".")
                if parts[0] == "onepledge_datagen" and len(parts) > 1 and parts[1] in LAYERS:
                    assert parts[1] in ALLOWED_INTERNAL[layer], f"{layer}/{path.name} imports {name}"


def test_the_generator_reads_no_environment_and_uses_no_clock_or_os_randomness() -> None:
    for path in SOURCE.rglob("*.py"):
        text = path.read_text()
        for forbidden in (
            "os.environ",
            "getenv",
            "datetime.now",
            "time.time",
            "uuid4(",
            "os.urandom",
            "secrets.",
        ):
            assert forbidden not in text, f"{path.name} uses {forbidden}"


def test_no_source_module_looks_like_a_test_module() -> None:
    assert not [p for p in SOURCE.rglob("*.py") if p.name.startswith("test_")]


def test_every_generator_error_derives_from_one_base() -> None:
    assert issubclass(TapeFormatError, DataGenError)
    assert TapeFormatError.code == "TAPE_FORMAT"
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Error"):
                bases = {ast.unparse(base) for base in node.bases}
                assert (
                    bases & {"DataGenError", "PlanError", "InvariantsViolatedError"}
                    or node.name == "DataGenError"
                ), f"{path.name}:{node.name} derives from {sorted(bases)}"


def test_the_oracle_reads_completely() -> None:
    for path in (SOURCE / "domain" / "oracle").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef | ast.FunctionDef) and not node.name.startswith("__"):
                assert ast.get_docstring(node), f"{path.name}:{node.name} has no docstring"
