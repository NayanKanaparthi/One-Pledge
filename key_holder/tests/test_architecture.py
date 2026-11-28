"""Layer split, import boundaries and the repository's pre-commit rules for this package (WP1).

The checker is tested on hand-written sources first, so a green run means the rules were
applied, not that nothing was looked at.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

OWN = "onepledge_key_holder"
PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE / "src" / OWN
MIGRATIONS = PACKAGE / "migrations"
REPOSITORY = PACKAGE.parent
SETTINGS_MODULE = SOURCE / "infrastructure" / "settings.py"
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
SQL_PACKAGES = frozenset({"sqlalchemy", "psycopg", "alembic"})
WEB_PACKAGES = frozenset({"fastapi", "starlette", "uvicorn"})
NETWORK_MODULES = frozenset({"socket", "ssl", "http", "urllib", "asyncio"})
TOOL_DIRECTORIES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis"})


def imported(source: str) -> set[str]:
    """Every module an absolute import of `source` names."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def boundary_problems(layer: str, source: str) -> list[str]:
    """What `source`, a module of `layer`, imports that its layer may not."""
    names = imported(source)
    roots = {name.split(".")[0] for name in names}
    own_layers = {name.split(".")[1] for name in names if name.startswith(OWN + ".")}
    third_party = {root for root in roots if root not in sys.stdlib_module_names and root != OWN}
    problems = [f"imports the layer 1 package {root}" for root in sorted(roots & LAYER_ONE)]
    if layer in ("domain", "application"):
        problems += [f"{layer} imports {root}" for root in sorted(third_party)]
        allowed = {"domain"} if layer == "domain" else {"domain", "application"}
        problems += [f"{layer} imports the {other} layer" for other in sorted(own_layers - allowed)]
    if layer == "domain":
        problems += [f"domain imports the network module {root}" for root in sorted(roots & NETWORK_MODULES)]
    if layer == "infrastructure" and "interface" in own_layers:
        problems.append("infrastructure imports the interface layer")
    if roots & SQL_PACKAGES and layer not in ("infrastructure", "migrations"):
        problems.append(f"{layer} imports SQL packages {sorted(roots & SQL_PACKAGES)}")
    if roots & WEB_PACKAGES and layer != "interface":
        problems.append(f"{layer} imports web packages {sorted(roots & WEB_PACKAGES)}")
    return problems


def layer_of(path: Path) -> str:
    if path.is_relative_to(MIGRATIONS):
        return "migrations"
    parts = path.relative_to(SOURCE).parts
    return parts[0] if len(parts) > 1 else "package"


def python_files() -> list[Path]:
    return sorted([*SOURCE.rglob("*.py"), *MIGRATIONS.rglob("*.py")])


@pytest.mark.parametrize(
    ("layer", "source"),
    [
        ("domain", "import sqlalchemy\n"),
        ("domain", "from sqlalchemy.orm import Session\n"),
        ("domain", "from fastapi import FastAPI\n"),
        ("domain", "import socket\n"),
        ("domain", f"from {OWN}.infrastructure.settings import ServeSettings\n"),
        ("application", "import psycopg\n"),
        ("application", f"from {OWN}.interface.http import create_app\n"),
        ("infrastructure", "from fastapi import FastAPI\n"),
        ("infrastructure", f"from {OWN}.interface.cli import main\n"),
        ("interface", "from onepledge_contract_fakes.key_holder.double import KeyHolderDouble\n"),
        ("interface", "import onepledge_bank_agent\n"),
        ("infrastructure", "from onepledge_outside_records.domain import errors\n"),
    ],
)
def test_the_checker_finds_each_forbidden_import(layer: str, source: str) -> None:
    assert boundary_problems(layer, source) != []


def test_the_checker_allows_what_each_layer_may_import() -> None:
    assert boundary_problems("domain", "from dataclasses import dataclass\nimport enum\n") == []
    assert boundary_problems("application", f"from {OWN}.domain.identity import KeyVersion\n") == []
    assert boundary_problems("infrastructure", "import sqlalchemy\n") == []
    assert boundary_problems("interface", "from fastapi import FastAPI\nimport uvicorn\n") == []


def test_every_module_keeps_its_layer_boundaries() -> None:
    found = {
        str(path.relative_to(PACKAGE)): problems
        for path in python_files()
        if (problems := boundary_problems(layer_of(path), path.read_text(encoding="utf-8")))
    }
    assert found == {}


def test_the_four_layers_exist() -> None:
    for layer in ("domain", "application", "infrastructure", "interface"):
        assert (SOURCE / layer / "__init__.py").is_file(), layer
    assert (SOURCE / "py.typed").is_file()


def test_the_environment_is_read_only_in_the_settings_module() -> None:
    for path in python_files():
        if path == SETTINGS_MODULE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv", "putenv"):
                pytest.fail(f"{path.name} reads the environment")


def test_every_module_has_a_docstring_and_no_type_check_is_suppressed() -> None:
    for path in python_files():
        source = path.read_text(encoding="utf-8")
        assert ast.get_docstring(ast.parse(source)), path.name
    for path in PACKAGE.rglob("*.py"):
        assert "type: " + "ignore" not in path.read_text(encoding="utf-8"), path.name


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


def test_the_em_dash_hook_names_the_character_for_every_text_file() -> None:
    """The hook's entry, compiled as pre-commit's pygrep compiles it (a bytes pattern searched
    over the file's bytes), finds U+2014 and not a hyphen."""
    hook = pre_commit_hook("no-em-dash")
    assert (hook["language"], hook["types"]) == ("pygrep", ["text"])
    pattern = re.compile(hook["entry"].encode())
    assert pattern.search("a new line \u2014 with the character".encode()) is not None
    assert pattern.search(b"a new line - with a hyphen") is None


def test_no_file_of_the_package_holds_an_em_dash() -> None:
    holding = [
        str(path.relative_to(PACKAGE)) for path in package_files() if "\u2014".encode() in path.read_bytes()
    ]
    assert holding == []


def test_the_env_file_hook_rejects_a_committed_env_file_in_this_package() -> None:
    hook = pre_commit_hook("no-env-file")
    assert hook["language"] == "fail"
    files, exclude = re.compile(hook["files"]), re.compile(hook["exclude"])
    for name in (".env", ".env.local", "migrations/.env"):
        path = f"{PACKAGE.name}/{name}"
        assert files.search(path) is not None and exclude.search(path) is None, path
    assert exclude.search(f"{PACKAGE.name}/.env.example") is not None


def test_the_package_holds_no_env_file() -> None:
    env_files = [
        str(path.relative_to(PACKAGE))
        for path in package_files()
        if re.fullmatch(r"\.env(\..*)?", path.name) and path.name != ".env.example"
    ]
    assert env_files == []
