"""The package can be tested and run from its own declaration (SPEC_DECISIONS 117, 120).

The root pytest configuration needs `pytest-timeout` and `onepledge-testtools`, and every third
party module the tests or the source import must be declared in `pyproject.toml`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from onepledge_testtools.declarations import undeclared_source_imports, undeclared_test_requirements

pytestmark = pytest.mark.unit

PACKAGE = Path(__file__).resolve().parents[2]


def test_the_tests_need_nothing_the_package_does_not_declare() -> None:
    assert undeclared_test_requirements(PACKAGE, PACKAGE / "tests") == []


@pytest.mark.parametrize("tree", ["src/onepledge_bank_agent", "migrations"])
def test_every_import_of_the_source_and_the_migrations_is_declared(tree: str) -> None:
    assert undeclared_source_imports(PACKAGE, PACKAGE / tree) == []
