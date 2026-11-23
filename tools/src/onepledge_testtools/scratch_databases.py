"""Scratch PostgreSQL databases and roles of test runs: their names and their sweep (SPEC_DECISIONS 76).

Every database and role a test creates is named `optest_<UTC yyyymmddHHMMSS>_<pid>_<8 hex>`
followed by its suffix, so an interrupted run's leftovers can be found by age. The session
sweep drops only names of that form older than two hours; `make test-sweep` (this module
run as a script) drops every `optest_` database and role, whatever its age, and prints how
many it dropped. No other name is ever touched.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
import psycopg.errors
from psycopg import sql

PREFIX = "optest_"
MAX_IDENTIFIER = 63
SESSION_SWEEP_AGE = timedelta(hours=2)
DEFAULT_ADMIN_URL = "postgresql://postgres@localhost:55432/postgres"
_STAMP = "%Y%m%d%H%M%S"
_TIMESTAMPED = re.compile(r"^optest_([0-9]{14})_[0-9]+_[0-9a-f]{8}(_|$)")


def admin_url() -> str:
    """The superuser URL of the test server: PG_ADMIN_URL, or the local development default."""
    return os.environ.get("PG_ADMIN_URL", DEFAULT_ADMIN_URL)


def new_name(suffix: str, now: datetime | None = None) -> str:
    """A fresh scratch name: `optest_<UTC timestamp>_<pid>_<8 hex>_<suffix>`.

    PostgreSQL keeps 63 bytes of an identifier, so a name that would be cut is refused;
    a caller that appends a role suffix such as `_owner` passes a suffix short enough.
    """
    moment = (now or datetime.now(UTC)).strftime(_STAMP)
    name = f"{PREFIX}{moment}_{os.getpid()}_{secrets.token_hex(4)}_{suffix}"
    if len(name.encode()) > MAX_IDENTIFIER:
        raise ValueError(f"the scratch name for suffix {suffix!r} is longer than {MAX_IDENTIFIER} bytes")
    return name


def created_at(name: str) -> datetime | None:
    """The creation time a scratch name carries, or None for any other name."""
    match = _TIMESTAMPED.match(name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), _STAMP).replace(tzinfo=UTC)


def is_stale(name: str, now: datetime, older_than: timedelta | None) -> bool:
    """Whether the sweep drops this name: every `optest_` name when `older_than` is None."""
    if not name.startswith(PREFIX):
        return False
    if older_than is None:
        return True
    stamp = created_at(name)
    return stamp is not None and now - stamp > older_than


@dataclass(frozen=True)
class SweepResult:
    """What a sweep did: how many objects it dropped and which it could not drop."""

    dropped: int
    failed: tuple[str, ...]


def sweep(url: str, older_than: timedelta | None, now: datetime | None = None) -> SweepResult:
    """Drop the stale scratch databases first, then the stale roles.

    A role that still holds a privilege in a database outside the sweep cannot be dropped;
    it is named in `failed` and the sweep goes on with the next one.
    """
    moment = now or datetime.now(UTC)
    dropped = 0
    failed: list[str] = []
    with psycopg.connect(url, autocommit=True, connect_timeout=3) as admin:
        databases = admin.execute("SELECT datname FROM pg_database WHERE starts_with(datname, %s)", (PREFIX,))
        for (name,) in databases.fetchall():
            if is_stale(name, moment, older_than):
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
                dropped += 1
        roles = admin.execute("SELECT rolname FROM pg_roles WHERE starts_with(rolname, %s)", (PREFIX,))
        for (name,) in roles.fetchall():
            if not is_stale(name, moment, older_than):
                continue
            try:
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))
            except psycopg.errors.DependentObjectsStillExist:
                failed.append(name)
            else:
                dropped += 1
    return SweepResult(dropped, tuple(failed))


def main() -> int:
    """`make test-sweep`: drop every `optest_` database and role and say how many."""
    try:
        result = sweep(admin_url(), older_than=None)
    except psycopg.OperationalError:
        sys.stderr.write("test-sweep: PostgreSQL is not reachable at PG_ADMIN_URL; nothing was dropped.\n")
        return 1
    sys.stdout.write(f"test-sweep: dropped {result.dropped} optest_ databases and roles\n")
    for name in result.failed:
        sys.stderr.write(f"test-sweep: role {name} still holds privileges in another database; not dropped\n")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
