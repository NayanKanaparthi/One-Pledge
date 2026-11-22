"""The sentences for a database that refuses a command, each naming the real cause (SPEC_DECISIONS 78).

A database that cannot be reached, one that refuses the connection, one that is reachable
but has no tables, and a role that lacks a privilege are different faults with different
sentences, in every command. Each names the variable, never its value.
"""

from __future__ import annotations

from onepledge_outside_records.domain.errors import OperatorError

UNDEFINED_TABLE = "42P01"
INSUFFICIENT_PRIVILEGE = "42501"
# libpq prefixes a message the server sent with its severity; a connection failure without one
# never reached a PostgreSQL server.
_SERVER_ANSWERED = "FATAL:"


def sqlstate(error: BaseException) -> str | None:
    """The SQLSTATE of a driver error, also when SQLAlchemy wraps it."""
    original = getattr(error, "orig", None) or error
    state = getattr(original, "sqlstate", None)
    return state if isinstance(state, str) else None


def connection_failed(error: BaseException, variable: str) -> OperatorError:
    """A connection that did not open: no server at the URL, or a server that refused it."""
    if _SERVER_ANSWERED in str(error):
        return OperatorError(
            f"PostgreSQL refused the connection of {variable}: its role, password or database "
            "is not set up; run db provision, or fix the URL.",
            "DATABASE_UNAVAILABLE",
            error,
        )
    return OperatorError(
        f"PostgreSQL is not reachable at {variable}; start it or fix the URL.", "DATABASE_UNAVAILABLE", error
    )


def tables_missing(error: BaseException | None = None) -> OperatorError:
    """The database is reachable but holds no outside records tables."""
    return OperatorError(
        "The outside records tables are missing; run db migrate first.", "NOT_MIGRATED", error
    )


def privilege_missing(variable: str, remedy: str, error: BaseException | None = None) -> OperatorError:
    """The role of `variable` may not do what the command needs."""
    return OperatorError(
        f"The role of {variable} lacks a privilege this command needs; {remedy}.",
        "CONFIGURATION_ERROR",
        error,
    )
