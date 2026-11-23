"""The session parameters the bank agent sets on every connection, and the pooled-connection guard.

SPEC_DECISIONS 103.7: the bank agent does not rely on role or database defaults. Every connection
of every engine it makes is opened with these parameters as connection options, which PostgreSQL
applies after (and so over) any role or database setting, before the first statement. The values
are written as `SHOW` prints them, so the startup check compares the session with this table as
it is. Every new pooled connection is also refused unless its session fires triggers
(`session_replication_role` is `origin`): a session that does not fire them would pass every
guard of the schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

import psycopg
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.pool import ConnectionPoolEntry

SESSION_PARAMETERS: Mapping[str, str] = MappingProxyType(
    {
        "search_path": "public",
        "statement_timeout": "5min",
        "lock_timeout": "1min",
        "idle_in_transaction_session_timeout": "10min",
    }
)
_SHOWN = text(
    "SELECT current_setting('search_path'), current_setting('statement_timeout'), "
    "current_setting('lock_timeout'), current_setting('idle_in_transaction_session_timeout')"
)
_REPLICATION_ROLE = "SELECT current_setting('session_replication_role')"


def connect_options() -> str:
    """The libpq `options` value that sets every session parameter on a new connection."""
    return " ".join(f"-c {name}={value}" for name, value in SESSION_PARAMETERS.items())


def session_parameter_problems(connection: Connection) -> list[str]:
    """The names of the session parameters whose value differs from the table, in table order."""
    shown = connection.execute(_SHOWN).one()
    return [
        name
        for (name, expected), value in zip(SESSION_PARAMETERS.items(), shown, strict=True)
        if value != expected
    ]


def _refuse_replica(dbapi_connection: psycopg.Connection[tuple[object, ...]], _: ConnectionPoolEntry) -> None:
    """A new pooled connection whose session would not fire triggers is closed and refused."""
    with dbapi_connection.cursor() as cursor:
        cursor.execute(_REPLICATION_ROLE)
        row = cursor.fetchone()
    dbapi_connection.rollback()  # leave the connection idle, as the pool expects a new one
    if row is None or row[0] != "origin":
        dbapi_connection.close()
        raise psycopg.OperationalError("the session does not fire triggers")


def refuse_replica_sessions(engine: Engine) -> None:
    """Check `session_replication_role` on every new connection of `engine` (SPEC_DECISIONS 103.7)."""
    event.listen(engine, "connect", _refuse_replica)
