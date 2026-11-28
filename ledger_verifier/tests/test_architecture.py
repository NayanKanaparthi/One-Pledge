"""Import boundaries and the repository's pre-commit rules for the ledger verifier (WP1).

The checker is tested on hand-written sources first, so a green run means the rules were
applied, not that nothing was looked at.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

OWN = "onepledge_ledger_verifier"
PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE / "src" / OWN
REPOSITORY = PACKAGE.parent
# SPEC section 3: no layer 2 `src/` imports a layer 1 package.
LAYER_ONE = frozenset(
    {
        "onepledge_bank_agent",
        "onepledge_datagen",
        "onepledge_outside_records",
        "onepledge_evaluation",
        "onepledge_contract_fakes",
        "onepledge_testtools",
    }
)
# The verifier is a command, not a service.
WEB_PACKAGES = frozenset({"fastapi", "starlette", "uvicorn"})
TOOL_DIRECTORIES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis"})


def boundary_problems(source: str) -> list[str]:
    """What `source` imports that the verifier may not."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return [f"imports {root}" for root in sorted(roots & (LAYER_ONE | WEB_PACKAGES))]


@pytest.mark.parametrize(
    "source",
    [
        "from onepledge_contract_fakes.registry import ledger\n",
        "import onepledge_bank_agent.infrastructure.crypto.signatures\n",
        "from fastapi import FastAPI\n",
    ],
)
def test_the_checker_finds_each_forbidden_import(source: str) -> None:
    assert boundary_problems(source) != []


def test_every_module_keeps_the_boundaries_and_has_a_docstring() -> None:
    modules = sorted(SOURCE.rglob("*.py"))
    assert modules
    for path in modules:
        source = path.read_text(encoding="utf-8")
        assert boundary_problems(source) == [], path.name
        assert ast.get_docstring(ast.parse(source)), path.name
    assert (SOURCE / "py.typed").is_file()


def pre_commit_hook(hook_id: str) -> dict[str, Any]:
    document = yaml.safe_load((REPOSITORY / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {hook["id"]: hook for repo in document["repos"] for hook in repo["hooks"]}
    hook: dict[str, Any] = hooks[hook_id]
    return hook


def package_files() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE.rglob("*")
        if path.is_file()
        and not TOOL_DIRECTORIES & set(path.parts)
        and not any(part.endswith(".egg-info") for part in path.parts)
    )


def test_no_file_of_the_package_holds_an_em_dash() -> None:
    """The `no-em-dash` hook's entry, compiled as pygrep compiles it (bytes), finds U+2014, and
    no file of the package holds the character."""
    hook = pre_commit_hook("no-em-dash")
    assert re.compile(hook["entry"].encode()).search("x \u2014 y".encode()) is not None
    holding = [str(p.relative_to(PACKAGE)) for p in package_files() if "\u2014".encode() in p.read_bytes()]
    assert holding == []


def test_the_package_holds_no_env_file_which_the_hook_rejects() -> None:
    hook = pre_commit_hook("no-env-file")
    files, exclude = re.compile(hook["files"]), re.compile(hook["exclude"])
    assert files.search(f"{PACKAGE.name}/.env") is not None
    assert exclude.search(f"{PACKAGE.name}/.env") is None
    assert [p.name for p in package_files() if files.search(p.name) and not exclude.search(p.name)] == []
