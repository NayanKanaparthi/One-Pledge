"""Import boundaries and environment access of the doubles package (SPEC 2.1, 21.6)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SOURCE = Path(__file__).resolve().parents[1] / "src" / "onepledge_contract_fakes"
OTHER_PACKAGES = (
    "onepledge_bank_agent",
    "onepledge_datagen",
    "onepledge_outside_records",
    "onepledge_evaluation",
)


def modules() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def imported_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_other_onepledge_package_is_imported() -> None:
    for path in modules():
        offending = {n for n in imported_names(path) if n.split(".")[0] in OTHER_PACKAGES}
        assert not offending, f"{path.relative_to(SOURCE)} imports {sorted(offending)}"


def test_os_environ_is_read_only_in_the_settings_module() -> None:
    for path in modules():
        if path == SOURCE / "settings.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
                pytest.fail(f"{path.relative_to(SOURCE)} reads the environment")


def test_no_source_module_looks_like_a_test_module() -> None:
    assert not [p for p in modules() if p.name.startswith("test_") or p.name.endswith("_test.py")]


def test_every_module_says_it_is_a_test_double() -> None:
    for path in modules():
        docstring = ast.get_docstring(ast.parse(path.read_text())) or ""
        text = docstring.lower()
        assert (
            "test double" in text or "test controls" in text or "contract checker" in text or "check" in text
        ), f"{path.relative_to(SOURCE)} does not say what it is"
