"""The pytest plugin (SPEC_DECISIONS 76 and 77), each check in a child pytest with the root configuration."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from onepledge_testtools import processes
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


def _child(tests: Path, *extra: str) -> list[str]:
    return [
        processes.PYTHON,
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
    return {**os.environ, **extra}


@pytest.mark.reliability
@pytest.mark.integration
def test_sigterm_tears_the_fixtures_down_so_the_database_is_dropped(tmp_path: Path) -> None:
    tests = tmp_path / "test_killed.py"
    tests.write_text(SIGTERM_TESTS)
    ready = tmp_path / "ready"
    child = processes.start(_child(tests), _environment(READY_FILE=str(ready)), merge_stderr=True)
    deadline = time.monotonic() + 60
    while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    name = ready.read_text() if ready.exists() else ""
    child.send_signal(signal.SIGTERM)
    output, _ = child.communicate(timeout=60)
    assert name, output
    with psycopg.connect(admin_url(), autocommit=True) as admin:
        left = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if left is not None:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    assert left is None, output
    assert child.returncode == pytest.ExitCode.INTERRUPTED, output
    assert "received signal SIGTERM" in output


@pytest.mark.reliability
@pytest.mark.integration
def test_every_test_has_a_time_limit_and_e2e_tests_a_longer_one(tmp_path: Path) -> None:
    tests = tmp_path / "test_limits.py"
    tests.write_text(LIMIT_TESTS)
    started = time.monotonic()
    result = processes.run(_child(tests), _environment())
    assert time.monotonic() - started < 30
    assert "3 passed" in result.stdout, result.stdout
    assert "1 failed" in result.stdout, result.stdout
    assert "Timeout" in result.stdout, result.stdout


# SPEC_DECISIONS 115.5: SIGTERM at twenty moments of a running integration session, during
# provisioning among them; afterwards the server holds no object under that session's stem.
PROVISIONING_TESTS = """
import time

import psycopg
import pytest
from psycopg import sql

from onepledge_testtools.scratch_databases import admin_url, deferred_signals, new_name


def run(statement, *names):
    with psycopg.connect(admin_url(), autocommit=True) as admin:
        admin.execute(sql.SQL(statement).format(*(sql.Identifier(n) for n in names)))


@pytest.fixture
def provisioned():
    name = new_name("term")
    with deferred_signals():
        run("CREATE ROLE {}", name + "_owner")
        time.sleep(0.05)
        run("CREATE ROLE {}", name + "_app")
        time.sleep(0.05)
        run("CREATE DATABASE {} OWNER {}", name, name + "_owner")
        time.sleep(0.05)
        run("GRANT CONNECT ON DATABASE {} TO {}", name, name + "_app")
    yield name
    run("DROP DATABASE IF EXISTS {} WITH (FORCE)", name)
    run("DROP ROLE IF EXISTS {}", name + "_app")
    run("DROP ROLE IF EXISTS {}", name + "_owner")


@pytest.mark.integration
@pytest.mark.parametrize("round_", range(4))
def test_works_on_a_provisioned_database(provisioned, round_):
    time.sleep(0.15)
"""
MOMENTS = [0.05 + 0.15 * index for index in range(20)]


def leftovers(admin: psycopg.Connection[tuple[str]], pid: int) -> list[str]:
    pattern = f"optest\\\\_%\\\\_{pid}\\\\_%"
    rows = admin.execute(
        "SELECT datname FROM pg_database WHERE datname LIKE %s "
        "UNION ALL SELECT rolname FROM pg_roles WHERE rolname LIKE %s",
        (pattern, pattern),
    ).fetchall()
    return [row[0] for row in rows]


@pytest.mark.reliability
@pytest.mark.integration
@pytest.mark.timeout(300)
def test_sigterm_at_twenty_moments_leaves_nothing_under_the_session_stem(tmp_path: Path) -> None:
    tests = tmp_path / "test_provisioning.py"
    tests.write_text(PROVISIONING_TESTS)
    left: dict[float, list[str]] = {}
    with psycopg.connect(admin_url(), autocommit=True) as admin:
        for moment in MOMENTS:
            child = processes.start(_child(tests), _environment(), merge_stderr=True)
            time.sleep(moment)
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)
            child.communicate(timeout=60)
            found = leftovers(admin, child.pid)
            if found:
                left[moment] = found
    assert left == {}
