"""Every public method of every repository port has a parity case (SPEC_DECISIONS 63, 127).

The repository ports are the `*Repository` protocols of `application/ports.py` and
`application/evidence_ports.py`, the `HistoryReader` deep verification reads through, and the
`UnitOfWork`'s own methods. A parity case is a test of `tests/ports/` (run against the in-memory
store and PostgreSQL by the `ports` fixture) that names the methods it covers with
`@parity("Port.method")`. A method without a case, a name that is no port method, or a case that
does not take the `ports` fixture fails here; so a read added for deep verification without its
case fails before the two implementations can drift apart.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from onepledge_bank_agent.application import evidence_ports, ports

pytestmark = pytest.mark.unit

PORT_TESTS = Path(__file__).resolve().parents[1] / "ports"
EXTRA_PORTS = ("HistoryReader", "UnitOfWork")


def port_methods() -> set[str]:
    """`Port.method` for every public method (not a property) of every repository port."""
    found: set[str] = set()
    for module in (ports, evidence_ports):
        for name, value in vars(module).items():
            is_port = name.endswith("Repository") or name in EXTRA_PORTS
            if inspect.isclass(value) and value.__module__ == module.__name__ and is_port:
                assert getattr(value, "_is_protocol", False), name
                found |= {
                    f"{name}.{method}"
                    for method, member in vars(value).items()
                    if not method.startswith("_") and inspect.isfunction(member)
                }
    return found


def parity_cases() -> dict[str, set[str]]:
    """Each parity case of `tests/ports/` and the methods it names."""
    cases: dict[str, set[str]] = {}
    for path in sorted(PORT_TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for test in ast.walk(tree):
            if not isinstance(test, ast.FunctionDef) or not test.name.startswith("test_"):
                continue
            named = {
                str(argument.value)
                for decorator in test.decorator_list
                if isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == "parity"
                for argument in decorator.args
                if isinstance(argument, ast.Constant)
            }
            assert named, f"{path.name}::{test.name} names no port method with @parity"
            assert "ports" in {argument.arg for argument in test.args.args}, f"{test.name} runs on one store"
            cases[f"{path.name}::{test.name}"] = named
    return cases


def test_every_repository_port_method_has_a_parity_case_and_every_name_is_a_port_method() -> None:
    methods, cases = port_methods(), parity_cases()
    covered = set().union(*cases.values())
    assert sorted(methods - covered) == [], "port methods without a parity case"
    assert sorted(covered - methods) == [], "names that are no port method"


def test_the_deep_verification_reads_are_ports_with_cases() -> None:
    reads = {"HistoryReader.stage_rows", "HistoryReader.request_rows", "HistoryReader.reference_rows"}
    assert reads <= port_methods()
    covered = set().union(*parity_cases().values())
    assert reads <= covered


def test_a_method_added_to_a_port_without_a_case_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check itself: a new method on a port is seen at once."""

    def added(self: object) -> None:
        del self

    monkeypatch.setattr(evidence_ports.HistoryReader, "new_read", added, raising=False)
    assert "HistoryReader.new_read" in port_methods() - set().union(*parity_cases().values())
