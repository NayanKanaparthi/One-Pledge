"""What a `unit` test may not do (SPEC_DECISIONS 77): network, database, child process, file writes.

A package's conftest calls `enforce(request, monkeypatch)` from an autouse fixture. For a test
marked `unit` it refuses `tmp_path` and `tmp_path_factory` and replaces socket connection and
binding and child process creation with an error, so a test that does I/O fails instead of
quietly being counted as a unit test. Reading files committed in the repository stays allowed.
"""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Callable

import pytest

WRITE_FIXTURES = frozenset({"tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory"})


class UnitIsolationError(AssertionError):
    """A test marked unit did I/O that decision 77 does not allow."""


def _refuse(what: str) -> Callable[..., None]:
    def refuse(*_: object, **__: object) -> None:
        raise UnitIsolationError(f"a unit test may not {what}; mark it integration")

    return refuse


def enforce(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply the unit rules to the current test when it is marked `unit`."""
    if request.node.get_closest_marker("unit") is None:
        return
    used = WRITE_FIXTURES & set(request.fixturenames)
    if used:
        named = ", ".join(sorted(used))
        raise UnitIsolationError(f"a unit test may not write files ({named}); mark it integration")
    monkeypatch.setattr(socket.socket, "connect", _refuse("open a network connection"))
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse("open a network connection"))
    monkeypatch.setattr(socket.socket, "bind", _refuse("bind a socket"))
    monkeypatch.setattr(subprocess.Popen, "__init__", _refuse("start a child process"))
