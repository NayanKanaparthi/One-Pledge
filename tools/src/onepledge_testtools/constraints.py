"""The tested dependency set: `constraints.txt` at the repository root (SPEC_DECISIONS 136).

`make setup` installs every package with `-c constraints.txt`, so a fresh environment gets the
exact versions `make ci` last passed with, transitive dependencies included, instead of whatever
the index holds that day. The file is written by `make constraints` from a new virtual
environment and holds `name==version` lines and comments only.

`constraint_problems` checks the file against the declarations: every distribution a package
declares (runtime and dev), other than the repository's own packages, has exactly one exact pin.
`starlette_client_problems` holds the one declaration rule the pins do not show: Starlette's TestClient
(`fastapi.testclient`) is built on httpx2 and warns that plain httpx is deprecated when httpx2 is
missing, so a package whose code imports it declares httpx2.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

from onepledge_testtools.declarations import declared, normalise

CONSTRAINTS_FILE = "constraints.txt"
_PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[A-Za-z0-9][A-Za-z0-9.+!_-]*)$")
TESTCLIENT_MODULES = frozenset({"fastapi.testclient", "starlette.testclient"})
TESTCLIENT_TRANSPORT = "httpx2"


def read_pins(text: str) -> tuple[dict[str, str], list[str]]:
    """The pins of a constraints file, normalised name to version, and one problem per bad line."""
    pins: dict[str, str] = {}
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN.match(line)
        if match is None:
            problems.append(f"line {number} is not name==version: {line}")
            continue
        name = normalise(match.group("name"))
        if name in pins:
            problems.append(f"line {number} pins {name} a second time")
        pins[name] = match.group("version")
    return pins, problems


def own_names(packages: Iterable[Path]) -> set[str]:
    """The distribution names of the repository's own packages, installed editable, never pinned."""
    names: set[str] = set()
    for package in packages:
        text = (package / "pyproject.toml").read_text(encoding="utf-8")
        found = re.search(r'^name = "([^"]+)"', text, flags=re.MULTILINE)
        if found:
            names.add(normalise(found.group(1)))
    return names


def constraint_problems(repository: Path, packages: Iterable[Path]) -> list[str]:
    """Every way `constraints.txt` fails to pin what the packages declare, one sentence each."""
    path = repository / CONSTRAINTS_FILE
    if not path.is_file():
        return [f"{CONSTRAINTS_FILE} does not exist"]
    pins, problems = read_pins(path.read_text(encoding="utf-8"))
    listed = list(packages)
    own = own_names(listed)
    for package in listed:
        for name in sorted(declared(package) - own):
            if name not in pins:
                problems.append(f"{package.name} declares {name}, which {CONSTRAINTS_FILE} does not pin")
    problems.extend(
        f"{CONSTRAINTS_FILE} pins {name}, a package of this repository" for name in sorted(own & set(pins))
    )
    return problems


def imported_module_paths(files: Iterable[Path]) -> set[str]:
    """Every absolute module path the files import, in full (`fastapi.testclient`, not `fastapi`)."""
    names: set[str] = set()
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module)
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def starlette_client_problems(package: Path, trees: Iterable[Path]) -> list[str]:
    """A package whose code imports Starlette's TestClient and does not declare httpx2."""
    files = [path for tree in trees for path in sorted(tree.rglob("*.py"))]
    used = sorted(TESTCLIENT_MODULES & imported_module_paths(files))
    if used and TESTCLIENT_TRANSPORT not in declared(package):
        return [f"{package.name} imports {used[0]} and does not declare {TESTCLIENT_TRANSPORT}"]
    return []
