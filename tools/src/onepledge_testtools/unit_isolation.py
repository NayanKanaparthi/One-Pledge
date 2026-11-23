"""What a test marked `unit` may not do, and how that is enforced (SPEC_DECISIONS 77, 116).

While a `unit` test runs (from its first fixture to its last teardown), an interpreter audit
hook and replaced database entry points refuse each of these, and the test fails naming the
rule:

- network: binding, connecting or sending on a socket, TCP or UDP (audit events
  `socket.bind`, `socket.connect`, `socket.connect_ex`, `socket.sendto`, `socket.sendmsg`);
- database: `psycopg.connect`, `psycopg.Connection.connect` and `sqlalchemy.create_engine`,
  replaced for the duration of the test;
- child processes: `subprocess`, `os.system`, `os.posix_spawn`, `os.spawn*`, `os.fork`,
  `os.forkpty` and `os.exec*`;
- the file system: opening a file to create or write it (`open`, `os.open`, `Path.write_*`,
  `tempfile`), and creating, renaming or removing a file or directory (`os.mkdir`,
  `os.rename`, `os.replace`, `os.remove`, `os.unlink`, `os.rmdir`, `os.truncate`,
  `os.symlink`, `os.link`, `shutil.rmtree`); and the fixtures `tmp_path`, `tmp_path_factory`,
  `tmpdir` and `tmpdir_factory`.

Reading files is not refused. Let through by name, because they are caches of tools and not
something a test does: the interpreter's bytecode cache (`__pycache__`) and Hypothesis's own
directory (`.hypothesis`). Not intercepted: a write to a file descriptor opened before the
test started; a module that imported `create_engine` by name before the test began keeps the
original function (a connection it opens is still refused at psycopg); and anything a C
extension does without raising an audit event.

A refused operation raises `UnitIsolationError` (a `PermissionError`); if the test still
passed because code under test swallowed it, the fixture fails the test at teardown.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import pytest
import sqlalchemy
import sqlalchemy.engine
import sqlalchemy.engine.create

# Whether the call phase of the current test passed, stored by the plugin's report hook.
CALL_PASSED = pytest.StashKey[bool]()
WRITE_FIXTURES = frozenset({"tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory"})
_NETWORK = frozenset(
    {"socket.bind", "socket.connect", "socket.connect_ex", "socket.sendto", "socket.sendmsg"}
)
_PROCESSES = frozenset(
    {"subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "os.exec"}
)
_FILES = frozenset(
    {
        "os.mkdir",
        "os.rename",
        "os.replace",
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "os.truncate",
        "os.symlink",
        "os.link",
        "shutil.rmtree",
        "tempfile.mkstemp",
        "tempfile.mkdtemp",
    }
)
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_WRITE_MODES = frozenset("wax+")
# Caches of tools, let through by name (see the module docstring).
_TOOL_CACHES = frozenset({"__pycache__", ".hypothesis"})


class UnitIsolationError(PermissionError):
    """A test marked unit did something SPEC_DECISIONS 77 and 116 do not allow."""


@dataclass
class _State:
    active: bool = False
    refused: list[str] = field(default_factory=list)


_STATE = _State()


ADVICE = "mark it integration"


def _refuse(rule: str) -> None:
    message = f"a unit test may not {rule}; {ADVICE}"
    _STATE.refused.append(message)
    raise UnitIsolationError(message)


def _in_tool_cache(path: object) -> bool:
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    return isinstance(path, (str, os.PathLike)) and bool(_TOOL_CACHES & set(Path(path).parts))


def _opens_for_writing(arguments: tuple[object, ...]) -> bool:
    path, mode, flags = (*arguments, None, None, None)[:3]
    if isinstance(path, int) or _in_tool_cache(path):
        return False
    if isinstance(mode, str) and _WRITE_MODES & set(mode):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _audit(event: str, arguments: tuple[object, ...]) -> None:
    if not _STATE.active:
        return
    if event in _NETWORK:
        _refuse("use the network")
    elif event in _PROCESSES:
        _refuse("start a child process")
    elif (event in _FILES and not (arguments and _in_tool_cache(arguments[0]))) or (
        event == "open" and _opens_for_writing(arguments)
    ):
        _refuse("create, write, rename or remove a file")


sys.addaudithook(_audit)


def _database(*_: object, **__: object) -> None:
    _refuse("connect to a database")


_DATABASE_ENTRY_POINTS: tuple[tuple[object, str], ...] = (
    (psycopg, "connect"),
    (psycopg.Connection, "connect"),
    (sqlalchemy, "create_engine"),
    (sqlalchemy.engine, "create_engine"),
    (sqlalchemy.engine.create, "create_engine"),
)


@contextmanager
def _active() -> Iterator[list[str]]:
    _STATE.refused = []
    _STATE.active = True
    try:
        yield _STATE.refused
    finally:
        _STATE.active = False


def enforce(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hold the current test to the unit rules while it runs, when it is marked `unit`."""
    if request.node.get_closest_marker("unit") is None:
        yield
        return
    used = WRITE_FIXTURES & set(request.fixturenames)
    if used:
        named = ", ".join(sorted(used))
        pytest.fail(
            f"a unit test may not create, write, rename or remove a file ({named}); mark it integration"
        )
    replace: Callable[[object, str, object], None] = monkeypatch.setattr
    for owner, name in _DATABASE_ENTRY_POINTS:
        replace(owner, name, _database)
    with _active() as refused:
        yield
    if refused and request.node.stash.get(CALL_PASSED, False):
        pytest.fail(refused[0])
