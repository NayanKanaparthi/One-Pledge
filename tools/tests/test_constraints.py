"""The tested dependency set: `constraints.txt`, how `make setup` uses it, httpx2 (SPEC_DECISIONS 136)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from onepledge_testtools.constraints import (
    CONSTRAINTS_FILE,
    constraint_problems,
    read_pins,
    starlette_client_problems,
)
from onepledge_testtools.processes import run

REPOSITORY = Path(__file__).resolve().parents[2]


def _packages() -> list[Path]:
    """The packages `make setup` installs, read from the Makefile's own `PACKAGES` line."""
    found = re.search(
        r"^PACKAGES := (.+)$", (REPOSITORY / "Makefile").read_text(encoding="utf-8"), re.MULTILINE
    )
    assert found is not None
    return [REPOSITORY / name for name in found.group(1).split()]


@pytest.mark.unit
def test_a_constraints_file_holds_exact_pins_and_comments_only() -> None:
    text = "# a comment\n\nPyYAML==6.0.3\nstarlette==1.10.0\nhttpx>=0.27\npyyaml==6.0.2\n"
    pins, problems = read_pins(text)
    assert pins == {"pyyaml": "6.0.2", "starlette": "1.10.0"}
    assert problems == ["line 5 is not name==version: httpx>=0.27", "line 6 pins pyyaml a second time"]


@pytest.mark.unit
def test_the_constraints_file_pins_every_declared_dependency_of_every_package() -> None:
    packages = _packages()
    assert [p.name for p in packages] == ["tools", "fakes", "datagen", "outside_records", "bank_agent"]
    assert constraint_problems(REPOSITORY, packages) == []


@pytest.mark.unit
def test_every_package_that_uses_starlettes_test_client_declares_httpx2() -> None:
    problems = [
        problem
        for package in _packages()
        for problem in starlette_client_problems(package, [package / "src", package / "tests"])
    ]
    assert problems == []


@pytest.mark.integration
@pytest.mark.parametrize(
    ("declared", "source", "problems"),
    [
        (["fastapi"], "from fastapi.testclient import TestClient\n", ["demo imports fastapi.testclient"]),
        (["fastapi"], "from fastapi import testclient\n", ["demo imports fastapi.testclient"]),
        (["starlette"], "import starlette.testclient\n", ["demo imports starlette.testclient"]),
        (["fastapi", "httpx2"], "from fastapi.testclient import TestClient\n", []),
        (["fastapi"], "from fastapi import FastAPI\n", []),
    ],
)
def test_the_test_client_rule_finds_each_form_of_import(
    tmp_path: Path, declared: list[str], source: str, problems: list[str]
) -> None:
    package = tmp_path / "demo"
    (package / "tests").mkdir(parents=True)
    quoted = ", ".join(f'"{d}"' for d in declared)
    (package / "pyproject.toml").write_text(f'[project]\nname = "demo"\ndependencies = [{quoted}]\n')
    (package / "tests" / "test_x.py").write_text(source)
    expected = [f"{problem} and does not declare httpx2" for problem in problems]
    assert starlette_client_problems(package, [package / "tests"]) == expected


@pytest.mark.integration
def test_a_missing_constraints_file_is_one_problem(tmp_path: Path) -> None:
    assert constraint_problems(tmp_path, []) == [f"{CONSTRAINTS_FILE} does not exist"]


@pytest.mark.integration
def test_make_setup_installs_every_package_with_the_constraints_file() -> None:
    """`PYTHON=echo` prints each install command `make setup` would run, instead of running it."""
    result = run(["make", "-s", "--no-print-directory", "-C", str(REPOSITORY), "setup", "PYTHON=echo"])
    assert result.returncode == 0, result.stderr
    installs = [line for line in result.stdout.splitlines() if line.startswith("-m pip install")]
    constraints = REPOSITORY / CONSTRAINTS_FILE
    assert len(installs) == len(_packages())
    for line, package in zip(installs, _packages(), strict=True):
        assert f"-c {constraints} " in line and line.endswith(f"-e {package.relative_to(REPOSITORY)}[dev]")
