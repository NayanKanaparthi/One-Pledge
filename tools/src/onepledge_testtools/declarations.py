"""Every package can be tested from its own declaration (SPEC_DECISIONS 117 and 120).

`undeclared_test_requirements` lists what a package's tests need and its `pyproject.toml` does
not declare: the pytest plugins the root configuration relies on (`pytest-timeout` for the
`timeout` setting, `onepledge-testtools` for the root `conftest.py`, `pytest-cov` for the
coverage gates of `make test`) and the distribution of every third party module its tests
import. `undeclared_source_imports` does the same for the runtime dependencies of `src/`, and
`pydantic_core_problems` holds the pydantic-core rule of SPEC_DECISIONS 128.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
import tomllib
from collections.abc import Iterable
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT_PLUGINS = ("pytest", "pytest-timeout", "pytest-cov", "onepledge-testtools")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def normalise(name: str) -> str:
    """A distribution name in the normal form of PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    match = _NAME.match(requirement.strip())
    return normalise(match.group(0)) if match else ""


def declared(package: Path, extras: Iterable[str] = ("dev",)) -> set[str]:
    """The distributions a package's `pyproject.toml` declares: runtime, and the named extras."""
    project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = {_requirement_name(r) for r in project.get("dependencies", [])}
    optional = project.get("optional-dependencies", {})
    for extra in extras:
        names |= {_requirement_name(r) for r in optional.get(extra, [])}
    return names | {normalise(project["name"])}


def runtime_declared(package: Path) -> set[str]:
    """The runtime distributions of a package, its own name included."""
    return declared(package, extras=())


def imported_modules(files: Iterable[Path]) -> set[str]:
    """The top level module names the files import with an absolute import."""
    names: set[str] = set()
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


def _distributions_of(modules: Iterable[str], local: set[str]) -> dict[str, set[str]]:
    providers = packages_distributions()
    needed: dict[str, set[str]] = {}
    for module in sorted(modules):
        if module in sys.stdlib_module_names or module in local or module == "__future__":
            continue
        needed[module] = {normalise(d) for d in providers.get(module, [])}
    return needed


def _local_modules(tree: Path) -> set[str]:
    return {p.stem for p in tree.rglob("*.py")} | {p.name for p in tree.rglob("*") if p.is_dir()}


def _missing(needed: dict[str, set[str]], have: set[str]) -> list[str]:
    problems = []
    for module, distributions in needed.items():
        if not distributions:
            problems.append(f"imports {module}, which no installed distribution provides")
        elif not distributions & have:
            problems.append(
                f"imports {module} from {', '.join(sorted(distributions))}, which is not declared"
            )
    return problems


def undeclared_test_requirements(package: Path, tests: Path) -> list[str]:
    """What the package's tests need and its runtime and dev declarations do not name."""
    have = declared(package)
    problems = [
        f"needs the plugin {plugin}, which is not declared" for plugin in ROOT_PLUGINS if plugin not in have
    ]
    modules = imported_modules(tests.rglob("*.py"))
    return problems + _missing(_distributions_of(modules, _local_modules(tests)), have)


def undeclared_source_imports(package: Path, source: Path) -> list[str]:
    """What the package's `src/` imports and its runtime dependencies do not name."""
    modules = imported_modules(source.rglob("*.py"))
    return _missing(_distributions_of(modules, _local_modules(source)), runtime_declared(package))


def _names_from(files: Iterable[Path], module: str) -> set[str]:
    names: set[str] = set()
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == module and node.level == 0:
                names.update(alias.name for alias in node.names)
    return names


def pydantic_core_problems(package: Path, source: Path) -> list[str]:
    """SPEC_DECISIONS 128: a name `pydantic` exports is imported from `pydantic`, and the package
    declares `pydantic-core` only when its source imports a name that exists nowhere else."""
    files = sorted(source.rglob("*.py"))
    pydantic = importlib.import_module("pydantic")
    problems = [
        f"imports {name} from pydantic_core, but pydantic exports it"
        for name in sorted(_names_from(files, "pydantic_core"))
        if hasattr(pydantic, name)
    ]
    if "pydantic-core" in runtime_declared(package) and "pydantic_core" not in imported_modules(files):
        problems.append("declares pydantic-core, but nothing imports pydantic_core")
    return problems
