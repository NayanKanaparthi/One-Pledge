"""Scratch PostgreSQL databases and roles of test runs: names, registration, sweep (SPEC_DECISIONS 76, 115).

Every database and role a test creates is named `optest_<UTC yyyymmddHHMMSS>_<pid>_<8 hex>_<suffix>`.
The timestamp and pid are those of the test session, so every name of one session shares one
stem, `optest_<timestamp>_<pid>_`, and the session can drop everything it made by that stem when
it ends, whatever its fixtures did. A name is registered in the session's own list when it is
made, before anything is created with it, and `deferred_signals` holds SIGTERM back while a
fixture provisions, so the object being created when a signal arrives is always known.

The session sweep drops `optest_` names older than two hours, and every `optest_` name whose
timestamp cannot be read; it never touches any other name, and an error on one object is one
warning line, never a failure. `make test-sweep` (this module run as a script) drops every
`optest_` database and role, whatever its age, and prints how many it dropped.
"""

from __future__ import annotations

import os
import re
import secrets
import signal
import sys
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg import sql

PREFIX = "optest_"
MAX_IDENTIFIER = 63
SESSION_SWEEP_AGE = timedelta(hours=2)
CLOCK_SKEW = timedelta(minutes=5)
DEFAULT_ADMIN_URL = "postgresql://postgres@localhost:55432/postgres"
_STAMP = "%Y%m%d%H%M%S"
_TIMESTAMP = re.compile(r"^optest_([0-9]{14})_")

type Warn = Callable[[str], None]


def admin_url() -> str:
    """The superuser URL of the test server: PG_ADMIN_URL, or the local development default."""
    return os.environ.get("PG_ADMIN_URL", DEFAULT_ADMIN_URL)


@dataclass
class _Session:
    """The stem and the registered names of this process's test session."""

    pid: int
    stem: str
    names: list[str] = field(default_factory=list)


_SESSIONS: dict[int, _Session] = {}


def _session() -> _Session:
    pid = os.getpid()
    if pid not in _SESSIONS:
        stamp = datetime.now(UTC).strftime(_STAMP)
        _SESSIONS[pid] = _Session(pid, f"{PREFIX}{stamp}_{pid}_")
    return _SESSIONS[pid]


def session_stem() -> str:
    """`optest_<UTC timestamp of the session's first name>_<pid>_`, the stem of every name it makes."""
    return _session().stem


def registered() -> tuple[str, ...]:
    """The names this session made, in order."""
    return tuple(_session().names)


def new_name(suffix: str, created: datetime | None = None) -> str:
    """A fresh scratch name, registered before anything is created with it.

    The name carries the session stem; a test that needs a name of another age passes
    `created`, and that name is registered all the same. PostgreSQL keeps 63 bytes of an
    identifier, so a name that would be cut is refused; a caller that appends a role suffix
    such as `_owner` passes a suffix short enough.
    """
    session = _session()
    stem = session.stem if created is None else f"{PREFIX}{created.strftime(_STAMP)}_{session.pid}_"
    name = f"{stem}{secrets.token_hex(4)}_{suffix}"
    if len(name.encode()) > MAX_IDENTIFIER:
        raise ValueError(f"the scratch name for suffix {suffix!r} is longer than {MAX_IDENTIFIER} bytes")
    session.names.append(name)
    return name


@contextmanager
def deferred_signals() -> Iterator[None]:
    """Hold SIGTERM and SIGINT back until the block ends; a signal sent meanwhile arrives after it."""
    blocked = {signal.SIGTERM, signal.SIGINT}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def created_at(name: str) -> datetime | None:
    """The creation time a scratch name carries, or None when it carries none that can be read."""
    match = _TIMESTAMP.match(name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), _STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


def is_stale(name: str, now: datetime, older_than: timedelta | None) -> bool:
    """Whether a sweep drops this name.

    Never a name outside the `optest_` prefix. With `older_than` None, every `optest_` name.
    Otherwise an `optest_` name older than `older_than`, or whose timestamp cannot be read or
    lies in the future.
    """
    if not name.startswith(PREFIX):
        return False
    if older_than is None:
        return True
    stamp = created_at(name)
    # A timestamp that cannot be read, or one from the future (a year of 9999), is not a session's.
    return stamp is None or stamp > now + CLOCK_SKEW or now - stamp > older_than


def select_stale(names: Iterable[str], now: datetime, older_than: timedelta | None) -> list[str]:
    """The names of a listing that a sweep drops, in order."""
    return [name for name in names if is_stale(name, now, older_than)]


@dataclass(frozen=True)
class SweepResult:
    """What a sweep did: how many objects it dropped and which it could not drop."""

    dropped: int
    failed: tuple[str, ...]


_DATABASES = "SELECT datname FROM pg_database WHERE starts_with(datname, %s) ORDER BY 1"
_ROLES = "SELECT rolname FROM pg_roles WHERE starts_with(rolname, %s) ORDER BY 1"


def _drop_all(
    admin: psycopg.Connection[tuple[str]], select: Callable[[list[str]], list[str]], prefix: str, warn: Warn
) -> SweepResult:
    dropped = 0
    failed: list[str] = []
    steps = ((_DATABASES, "DROP DATABASE IF EXISTS {} WITH (FORCE)"), (_ROLES, "DROP ROLE IF EXISTS {}"))
    for statement, drop in steps:
        names = [row[0] for row in admin.execute(statement, (prefix,)).fetchall()]
        for name in select(names):
            try:
                admin.execute(sql.SQL(drop).format(sql.Identifier(name)))
            except psycopg.Error as error:
                failed.append(name)
                warn(f"could not drop {name}: {type(error).__name__}")
            else:
                dropped += 1
    return SweepResult(dropped, tuple(failed))


def _ignore(_: str) -> None:
    return None


def sweep(
    url: str, older_than: timedelta | None, now: datetime | None = None, warn: Warn = _ignore
) -> SweepResult:
    """Drop the stale scratch databases first, then the stale roles; each failure is one warning."""
    moment = now or datetime.now(UTC)
    with psycopg.connect(url, autocommit=True, connect_timeout=3) as admin:
        return _drop_all(admin, lambda names: select_stale(names, moment, older_than), PREFIX, warn)


def drop_session(url: str, warn: Warn = _ignore) -> SweepResult:
    """Drop every database and role of this session: its stem and every name it registered."""
    session = _session()
    mine = set(session.names)
    with psycopg.connect(url, autocommit=True, connect_timeout=3) as admin:
        stem = _drop_all(admin, lambda names: names, session.stem, warn)
        extra = _drop_all(admin, lambda names: [n for n in names if n in mine], PREFIX, warn)
    return SweepResult(stem.dropped + extra.dropped, stem.failed + extra.failed)


def _to_stderr(line: str) -> None:
    sys.stderr.write(f"test-sweep: {line}\n")


def main() -> int:
    """`make test-sweep`: drop every `optest_` database and role and say how many."""
    try:
        result = sweep(admin_url(), older_than=None, warn=_to_stderr)
    except psycopg.OperationalError:
        sys.stderr.write("test-sweep: PostgreSQL is not reachable at PG_ADMIN_URL; nothing was dropped.\n")
        return 1
    sys.stdout.write(f"test-sweep: dropped {result.dropped} optest_ databases and roles\n")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
