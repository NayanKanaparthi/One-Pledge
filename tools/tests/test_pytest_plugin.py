"""The pytest plugin (SPEC_DECISIONS 76 and 77), each check in a child pytest with the root configuration."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from onepledge_testtools.scratch_databases import admin_url

REPOSITORY = Path(__file__).resolve().parents[2]
ROOT_CONFIG = REPOSITORY / "pyproject.toml"

SIGTERM_TESTS = """
import os
import time
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from onepledge_testtools.scratch_databases import admin_url, new_name


@pytest.fixture
def scratch():
    name = new_name("sigterm")
    with psycopg.connect(admin_url(), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield name
    with psycopg.connect(admin_url(), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


@pytest.mark.integration
def test_waits_to_be_killed(scratch):
    Path(os.environ["READY_FILE"]).write_text(scratch)
    time.sleep(100)
"""

LIMIT_TESTS = """
import time

import pytest


@pytest.mark.e2e
def test_an_e2e_test_gets_600_seconds(request):
    assert request.node.get_closest_marker("timeout").args == (600,)


@pytest.mark.e2e
@pytest.mark.timeout(5)
def test_an_e2e_test_keeps_its_own_limit(request):
    assert request.node.get_closest_marker("timeout").args == (5,)


@pytest.mark.unit
def test_every_other_test_gets_120_seconds(request):
    assert request.config.getini("timeout") == "120"
    assert request.node.get_closest_marker("timeout") is None


@pytest.mark.unit
@pytest.mark.timeout(1)
def test_a_hung_test_is_stopped():
    time.sleep(30)
"""

ISOLATION_TESTS = """
import socket
import subprocess

import pytest


@pytest.fixture(autouse=True)
def _isolation(enforce_unit_isolation):
    pass


@pytest.mark.unit
def test_unit_writes_a_file(tmp_path):
    (tmp_path / "x").write_text("x")


@pytest.mark.unit
def test_unit_connects():
    socket.create_connection(("127.0.0.1", 9), timeout=1)


@pytest.mark.unit
def test_unit_binds():
    socket.socket().bind(("127.0.0.1", 0))


@pytest.mark.unit
def test_unit_starts_a_process():
    subprocess.run(["true"], check=True)


@pytest.mark.unit
def test_unit_reads_a_committed_file():
    from pathlib import Path

    assert Path(__file__).read_text()


@pytest.mark.integration
def test_integration_may_do_all_of_it(tmp_path):
    (tmp_path / "x").write_text("x")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
    subprocess.run(["true"], check=True)
"""


def _child(tests: Path, *extra: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(ROOT_CONFIG),
        "--rootdir",
        str(REPOSITORY),
        "-p",
        "onepledge_testtools.pytest_plugin",
        "-q",
        "-p",
        "no:cacheprovider",
        *extra,
        str(tests),
    ]


def _environment(**extra: str) -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(REPOSITORY / "tools"), **extra}


@pytest.mark.reliability
@pytest.mark.integration
def test_sigterm_tears_the_fixtures_down_so_the_database_is_dropped(tmp_path: Path) -> None:
    tests = tmp_path / "test_killed.py"
    tests.write_text(SIGTERM_TESTS)
    ready = tmp_path / "ready"
    child = subprocess.Popen(  # noqa: S603 - the interpreter and arguments are fixed above
        _child(tests),
        env=_environment(READY_FILE=str(ready)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 60
    while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    name = ready.read_text() if ready.exists() else ""
    child.send_signal(signal.SIGTERM)
    output, _ = child.communicate(timeout=60)
    assert name, output.decode()
    with psycopg.connect(admin_url(), autocommit=True) as admin:
        left = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if left is not None:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    assert left is None, output.decode()
    assert child.returncode == pytest.ExitCode.INTERRUPTED, output.decode()
    assert "received signal SIGTERM" in output.decode()


@pytest.mark.reliability
@pytest.mark.integration
def test_every_test_has_a_time_limit_and_e2e_tests_a_longer_one(tmp_path: Path) -> None:
    tests = tmp_path / "test_limits.py"
    tests.write_text(LIMIT_TESTS)
    started = time.monotonic()
    result = subprocess.run(  # noqa: S603 - the interpreter and arguments are fixed above
        _child(tests), env=_environment(), capture_output=True, text=True, timeout=60, check=False
    )
    assert time.monotonic() - started < 30
    assert "3 passed" in result.stdout, result.stdout
    assert "1 failed" in result.stdout, result.stdout
    assert "Timeout" in result.stdout, result.stdout


@pytest.mark.integration
def test_a_unit_test_that_does_io_fails_and_reading_a_committed_file_does_not(tmp_path: Path) -> None:
    tests = tmp_path / "test_isolation.py"
    tests.write_text(ISOLATION_TESTS)
    result = subprocess.run(  # noqa: S603 - the interpreter and arguments are fixed above
        _child(tests, "-rA"), env=_environment(), capture_output=True, text=True, timeout=60, check=False
    )
    out = result.stdout
    assert "3 failed, 2 passed, 1 error" in out, out
    for test, what in (
        ("test_unit_writes_a_file", "a unit test may not write files (tmp_path, tmp_path_factory)"),
        ("test_unit_connects", "a unit test may not open a network connection"),
        ("test_unit_binds", "a unit test may not bind a socket"),
        ("test_unit_starts_a_process", "a unit test may not start a child process"),
    ):
        assert f"{test} - " in out and what in out, (test, out)
