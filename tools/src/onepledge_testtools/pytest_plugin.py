"""The repository's pytest plugin (SPEC_DECISIONS 76): SIGTERM teardown, stale sweep, e2e time limit.

The root `conftest.py` loads it for every package. It turns SIGTERM into the same orderly
stop as Ctrl-C, so fixtures still drop the databases and roles they made when a run is
killed; it drops `optest_` databases and roles a crashed run left more than two hours ago
before any test starts; it gives e2e tests 600 seconds where the root configuration
gives every other test 120; and it offers the `enforce_unit_isolation` fixture that holds
`unit` tests to SPEC_DECISIONS 77.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Generator, Iterator
from datetime import timedelta
from types import FrameType

import psycopg
import pytest

from onepledge_testtools.scratch_databases import (
    SESSION_SWEEP_AGE,
    admin_url,
    deferred_signals,
    drop_session,
    registered,
    sweep,
)
from onepledge_testtools.unit_isolation import CALL_PASSED, enforce

E2E_TIMEOUT_SECONDS = 600


class TerminatedError(KeyboardInterrupt):
    """The run received SIGTERM; raised as an interrupt so pytest tears every fixture down."""


_TERMINATING: list[bool] = []


def _terminate(signum: int, _: FrameType | None) -> None:
    # Only the first SIGTERM interrupts; later ones arrive while the session is already ending
    # and must not cut its final drop short.
    if not _TERMINATING:
        _TERMINATING.append(True)
        raise TerminatedError(f"received signal {signal.Signals(signum).name}")


def install_sigterm_handler() -> None:
    """Make SIGTERM interrupt the run like Ctrl-C does; only the main thread may install it."""
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _terminate)


def _write(session: pytest.Session, line: str) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(line)


def sweep_stale(session: pytest.Session, older_than: timedelta = SESSION_SWEEP_AGE) -> int:
    """Drop stale scratch objects; never fails: an unreachable server is 0, any error one warning line."""
    try:
        with deferred_signals():
            result = sweep(
                admin_url(), older_than, warn=lambda line: _write(session, f"optest sweep: {line}")
            )
    except psycopg.OperationalError:
        return 0
    except psycopg.Error as error:
        _write(session, f"optest sweep: stopped early: {type(error).__name__}")
        return 0
    return result.dropped


def pytest_configure(config: pytest.Config) -> None:
    """Install the SIGTERM handler before the first test runs."""
    del config
    install_sigterm_handler()


def pytest_sessionstart(session: pytest.Session) -> None:
    """Drop what crashed runs left behind; the count is shown when it is not zero."""
    dropped = sweep_stale(session)
    if dropped:
        _write(session, f"optest sweep: dropped {dropped} stale optest_ databases and roles")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    """After every fixture: drop whatever this session made, by its stem and its registered names.

    It runs on a normal end, a failure, SIGINT and SIGTERM (SPEC_DECISIONS 115.2); signals are
    held back while it runs, so it cannot be cut short.
    """
    if not registered():
        return
    try:
        with deferred_signals():
            result = drop_session(admin_url(), warn=lambda line: _write(session, f"optest cleanup: {line}"))
    except psycopg.Error as error:
        _write(session, f"optest cleanup: stopped early: {type(error).__name__}")
        return
    if result.dropped:
        _write(session, f"optest cleanup: dropped {result.dropped} databases and roles left by this session")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give e2e tests their longer limit unless the test sets its own."""
    for item in items:
        if item.get_closest_marker("e2e") is not None and item.get_closest_marker("timeout") is None:
            item.add_marker(pytest.mark.timeout(E2E_TIMEOUT_SECONDS))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Remember whether a test's call phase passed, for the unit isolation fixture's teardown."""
    report: pytest.TestReport = yield
    if call.when == "call":
        item.stash[CALL_PASSED] = report.passed
    return report


@pytest.fixture
def enforce_unit_isolation(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hold a `unit` test to SPEC_DECISIONS 77 and 116; a package's conftest makes it autouse."""
    yield from enforce(request, monkeypatch)
