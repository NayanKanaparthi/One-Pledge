"""Unit isolation (SPEC_DECISIONS 116): one probe per refused operation, as unit tests of a child pytest.

The child session holds one `unit` test per operation the module docstring of
`onepledge_testtools.unit_isolation` lists (the seven probes of WP1 review 4 among them), one
that swallows the error it gets, and one that reads a committed file. Each refused operation
must fail its test naming the rule; the read must pass.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from onepledge_testtools import processes, unit_isolation
from onepledge_testtools.scratch_databases import admin_url

REPOSITORY = Path(__file__).resolve().parents[2]
ROOT_CONFIG = REPOSITORY / "pyproject.toml"

PROBES = """
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import psycopg
import pytest
import sqlalchemy

SCRATCH = Path(os.environ["SCRATCH"])
pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolation(enforce_unit_isolation):
    pass


def test_tcp_connect():
    socket.create_connection(("127.0.0.1", 9), timeout=1)

def test_tcp_bind():
    socket.socket().bind(("127.0.0.1", 0))

def test_udp_sendto():
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("127.0.0.1", 9))

def test_psycopg_connect():
    psycopg.connect(os.environ["ADMIN_URL"])

def test_psycopg_connection_connect():
    psycopg.Connection.connect(os.environ["ADMIN_URL"])

def test_sqlalchemy_create_engine():
    sqlalchemy.create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none")

def test_subprocess():
    subprocess.run(["true"], check=True)

def test_os_system():
    os.system("true")

def test_os_posix_spawn():
    os.posix_spawn("/bin/true", ["true"], dict(os.environ))

def test_os_fork():
    if os.fork() == 0:
        os._exit(0)

def test_os_exec():
    os.execv("/bin/true", ["true"])

def test_path_write_text():
    (SCRATCH / "written.txt").write_text("x")

def test_open_for_append():
    with open(SCRATCH / "existing.txt", "a") as handle:
        handle.write("x")

def test_tempfile():
    tempfile.mkstemp()

def test_temporary_directory():
    tempfile.TemporaryDirectory()

def test_mkdir():
    (SCRATCH / "new_directory").mkdir()

def test_rename():
    (SCRATCH / "existing.txt").rename(SCRATCH / "renamed.txt")

def test_remove():
    (SCRATCH / "existing.txt").unlink()

def test_rmdir():
    (SCRATCH / "existing_directory").rmdir()

def test_rmtree():
    shutil.rmtree(SCRATCH / "existing_directory")

def test_tmp_path(tmp_path):
    pass

def test_swallowed_error():
    try:
        (SCRATCH / "swallowed.txt").write_text("x")
    except OSError:
        pass

def test_reads_a_committed_file():
    assert Path(os.environ["COMMITTED"]).read_text()
"""

RULES = {
    "test_tcp_connect": "use the network",
    "test_tcp_bind": "use the network",
    "test_udp_sendto": "use the network",
    "test_psycopg_connect": "connect to a database",
    "test_psycopg_connection_connect": "connect to a database",
    "test_sqlalchemy_create_engine": "connect to a database",
    "test_subprocess": "start a child process",
    "test_os_system": "start a child process",
    "test_os_posix_spawn": "start a child process",
    "test_os_fork": "start a child process",
    "test_os_exec": "start a child process",
    "test_path_write_text": "create, write, rename or remove a file",
    "test_open_for_append": "create, write, rename or remove a file",
    "test_tempfile": "create, write, rename or remove a file",
    "test_temporary_directory": "create, write, rename or remove a file",
    "test_mkdir": "create, write, rename or remove a file",
    "test_rename": "create, write, rename or remove a file",
    "test_remove": "create, write, rename or remove a file",
    "test_rmdir": "create, write, rename or remove a file",
    "test_rmtree": "create, write, rename or remove a file",
    "test_tmp_path": "create, write, rename or remove a file (tmp_path, tmp_path_factory)",
    "test_swallowed_error": "create, write, rename or remove a file",
}
OUTCOME = re.compile(r"^(PASSED|FAILED|ERROR) \S*::(test_\w+)(?: - (.*))?$")


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, tuple[str, str]]]:
    """Each probe's outcome and message, from one child pytest run with the repository configuration."""
    base = tmp_path_factory.mktemp("isolation")
    scratch = base / "scratch"
    scratch.mkdir()
    (scratch / "existing.txt").write_text("x")
    (scratch / "existing_directory").mkdir()
    tests = base / "test_probes.py"
    tests.write_text(PROBES)
    arguments = [processes.PYTHON, "-m", "pytest", "-c", str(ROOT_CONFIG), "--rootdir", str(REPOSITORY)]
    arguments += ["-p", "onepledge_testtools.pytest_plugin", "-p", "no:cacheprovider", "-rA", str(tests)]
    environment = {
        "SCRATCH": str(scratch),
        "ADMIN_URL": admin_url(),
        "COMMITTED": str(ROOT_CONFIG),
        "COLUMNS": "400",
    }
    result = processes.run(arguments, {**os.environ, **environment}, timeout=120)
    found: dict[str, tuple[str, str]] = {}
    for line in result.stdout.splitlines():
        match = OUTCOME.match(line)
        if match:
            found[match.group(2)] = (match.group(1), match.group(3) or "")
    yield found
    leftovers = sorted(p.name for p in scratch.iterdir())
    assert leftovers == ["existing.txt", "existing_directory"], leftovers


@pytest.mark.integration
@pytest.mark.parametrize("probe", sorted(RULES))
def test_each_refused_operation_fails_its_unit_test_naming_the_rule(
    outcomes: dict[str, tuple[str, str]], probe: str
) -> None:
    outcome, message = outcomes[probe]
    # A refused fixture fails at setup and a swallowed refusal at teardown (both ERROR); every
    # other probe fails in its call.
    assert outcome == ("ERROR" if probe in ("test_tmp_path", "test_swallowed_error") else "FAILED")
    assert f"a unit test may not {RULES[probe]}" in message, message


@pytest.mark.integration
def test_a_unit_test_may_read_a_committed_file(outcomes: dict[str, tuple[str, str]]) -> None:
    assert outcomes["test_reads_a_committed_file"][0] == "PASSED"


@pytest.mark.unit
def test_the_docstring_names_every_refused_operation_and_claims_nothing_beyond_it() -> None:
    doc = unit_isolation.__doc__ or ""
    for name in ("socket.sendto", "psycopg.connect", "sqlalchemy.create_engine", "os.fork", "os.posix_spawn"):
        assert name in doc
    assert "Not intercepted" in doc and "does I/O fails" not in doc
