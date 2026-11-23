"""The repository's pytest plugin (SPEC_DECISIONS 76): SIGTERM stop, stale sweep, e2e time limit.

The root `conftest.py` loads it for every package. A SIGTERM ends the session wherever it
lands (SPEC_DECISIONS 126): the handler records it and raises `TerminatedError`, a
`BaseException`, so `except Exception` cannot catch it; and since a bare `except:` still
could, the record is checked before every test starts and after every test ends. No further
test starts, the running test is reported as interrupted (never as passed or failed), every
fixture finalises, the session drops its scratch objects, the exit code is not zero and the
last line says the run was stopped by SIGTERM. During provisioning the signal is held and
acted on when the call returns (`deferred_signals`). The plugin also drops `optest_`
databases and roles a crashed run left more than two hours ago before any test starts; it
gives e2e tests 600 seconds where the root configuration gives every other test 120; and it
offers the `enforce_unit_isolation` fixture that holds `unit` tests to SPEC_DECISIONS 77.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from collections.abc import Generator, Iterator
from dataclasses import dataclass
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
    """The run received SIGTERM: a `BaseException`, so pytest stops and tears every fixture down."""


STOPPED = "stopped by SIGTERM"


@dataclass
class _Signalled:
    """The SIGTERM of this session, once received, and the test it interrupted."""

    received: bool = False
    interrupted: str | None = None
    raised: bool = False


_SIGNALLED = _Signalled()


def _terminate(signum: int, _: FrameType | None) -> None:
    _SIGNALLED.received = True
    # Only the first SIGTERM is raised; later ones arrive while the session is already ending
    # and must not cut its final drop short. The record is what the checks between tests read.
    if not _SIGNALLED.raised:
        _SIGNALLED.raised = True
        raise TerminatedError(f"received signal {signal.Signals(signum).name}")


def signalled() -> bool:
    """Whether this session has received SIGTERM."""
    return _SIGNALLED.received


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
    held back while it runs, so it cannot be cut short. After a SIGTERM the exit status is
    never zero.
    """
    if signalled() and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.INTERRUPTED
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
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, object, object]:
    """No test starts after a SIGTERM, and the session stops after the test that received it."""
    del nextitem
    if signalled():
        raise TerminatedError(STOPPED)
    outcome = yield
    if signalled():
        _SIGNALLED.interrupted = _SIGNALLED.interrupted or item.nodeid
        item.session.shouldstop = STOPPED
    return outcome


def _reraise_absorbed(item: pytest.Item) -> None:
    """A SIGTERM a bare `except:` absorbed during this phase is raised again, before the phase is
    reported, so the test counts as interrupted rather than passed."""
    if signalled():
        _SIGNALLED.interrupted = _SIGNALLED.interrupted or item.nodeid
        raise TerminatedError(STOPPED)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, None, None]:
    """Fixture setup: an absorbed SIGTERM is raised again when it ends."""
    yield
    _reraise_absorbed(item)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """The test itself: an absorbed SIGTERM is raised again when it ends."""
    yield
    _reraise_absorbed(item)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Remember whether a test's call phase passed, for the unit isolation fixture's teardown."""
    report: pytest.TestReport = yield
    if call.when == "call":
        item.stash[CALL_PASSED] = report.passed
    return report


def pytest_keyboard_interrupt(excinfo: pytest.ExceptionInfo[BaseException]) -> None:
    """Note which test a SIGTERM raised inside was running, for the last line."""
    del excinfo
    if signalled() and _SIGNALLED.interrupted is None:
        _SIGNALLED.interrupted = _current_test()


def _current_test() -> str | None:
    current = os.environ.get("PYTEST_CURRENT_TEST")
    return current.rsplit(" ", 1)[0] if current else None


def pytest_unconfigure(config: pytest.Config) -> None:
    """After the summary: the last line says the run did not complete (SPEC_DECISIONS 126.3)."""
    if not signalled():
        return
    during = f" during {_SIGNALLED.interrupted}" if _SIGNALLED.interrupted else ""
    line = f"{STOPPED}{during}; the run is not complete and no further test ran"
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(line)
    else:
        sys.stderr.write(line + "\n")


@pytest.fixture
def enforce_unit_isolation(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hold a `unit` test to SPEC_DECISIONS 77 and 116; a package's conftest makes it autouse."""
    yield from enforce(request, monkeypatch)
