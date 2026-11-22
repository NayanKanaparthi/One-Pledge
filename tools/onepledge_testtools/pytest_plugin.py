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
from datetime import timedelta
from types import FrameType

import psycopg
import pytest

from onepledge_testtools.scratch_databases import SESSION_SWEEP_AGE, admin_url, sweep
from onepledge_testtools.unit_isolation import enforce

E2E_TIMEOUT_SECONDS = 600


class TerminatedError(KeyboardInterrupt):
    """The run received SIGTERM; raised as an interrupt so pytest tears every fixture down."""


def _terminate(signum: int, _: FrameType | None) -> None:
    raise TerminatedError(f"received signal {signal.Signals(signum).name}")


def install_sigterm_handler() -> None:
    """Make SIGTERM interrupt the run like Ctrl-C does; only the main thread may install it."""
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _terminate)


def sweep_stale(older_than: timedelta = SESSION_SWEEP_AGE) -> int:
    """Drop scratch objects older than `older_than`; 0 when the test server is not reachable."""
    try:
        return sweep(admin_url(), older_than).dropped
    except psycopg.OperationalError:
        return 0


def pytest_configure(config: pytest.Config) -> None:
    """Install the SIGTERM handler before the first test runs."""
    del config
    install_sigterm_handler()


def pytest_sessionstart(session: pytest.Session) -> None:
    """Drop what crashed runs left behind; the count is shown when it is not zero."""
    dropped = sweep_stale()
    if dropped:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(f"optest sweep: dropped {dropped} stale optest_ databases and roles")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give e2e tests their longer limit unless the test sets its own."""
    for item in items:
        if item.get_closest_marker("e2e") is not None and item.get_closest_marker("timeout") is None:
            item.add_marker(pytest.mark.timeout(E2E_TIMEOUT_SECONDS))


@pytest.fixture
def enforce_unit_isolation(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold a `unit` test to SPEC_DECISIONS 77; a package's conftest makes it autouse."""
    enforce(request, monkeypatch)
