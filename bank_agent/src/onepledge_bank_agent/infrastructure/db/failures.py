"""The sentences for a database that refuses a command, each naming the real cause (SPEC_DECISIONS 78, 79).

A database that cannot be reached, one that refuses the connection, one that is reachable
but has no tables, and a role that lacks a privilege are different faults with different
sentences, in every command. Each names the variable, never its value. A migration that
fails for any reason is one sentence naming the revision.
"""

from __future__ import annotations

from onepledge_bank_agent.infrastructure.operator_errors import (
    CONFIGURATION_ERROR,
    DATABASE_UNAVAILABLE,
    OPERATION_FAILED,
    SCHEMA_NOT_CURRENT,
    OperatorError,
)

STARTUP_CHECK_FAILED = "STARTUP_CHECK_FAILED"
UNDEFINED_TABLE = "42P01"
INSUFFICIENT_PRIVILEGE = "42501"
SERVER_ANSWERED = "FATAL:"
"""libpq prefixes a message the server sent with its severity; a failure without it never reached
a PostgreSQL server."""


def sqlstate(error: BaseException) -> str | None:
    """The SQLSTATE of a driver error, also when SQLAlchemy wraps it."""
    original = getattr(error, "orig", None) or error
    state = getattr(original, "sqlstate", None)
    return state if isinstance(state, str) else None


def connection_failed(error: BaseException | None, variable: str) -> OperatorError:
    """A connection that did not open: no server at the URL, or a server that refused it."""
    if error is not None and SERVER_ANSWERED in str(error):
        return OperatorError(
            f"PostgreSQL refused the connection of {variable}: its role, password or database is not "
            "set up; run db provision, or fix the URL.",
            DATABASE_UNAVAILABLE,
        )
    return OperatorError(
        f"PostgreSQL is not reachable at {variable}; start it or fix the URL.", DATABASE_UNAVAILABLE
    )


def tables_missing() -> OperatorError:
    """The database is reachable but holds no bank agent tables."""
    return OperatorError("The bank database tables are missing; run db migrate first.", SCHEMA_NOT_CURRENT)


def privilege_missing(variable: str) -> OperatorError:
    """The role of `variable` may not do what the command needs."""
    return OperatorError(
        f"The role of {variable} lacks a privilege this command needs; run db migrate to restore the "
        "grants, or use the role the command expects.",
        CONFIGURATION_ERROR,
    )


def schema_mismatch() -> OperatorError:
    """A table, column or function the code expects is not there: the schema is behind the code."""
    return OperatorError(
        "The bank database does not have the shape this version expects; run db migrate.", SCHEMA_NOT_CURRENT
    )


def query_refused(state: str | None, variable: str) -> OperatorError:
    """The sentence for a statement PostgreSQL refused with a class 42 SQLSTATE."""
    if state == UNDEFINED_TABLE:
        return tables_missing()
    if state == INSUFFICIENT_PRIVILEGE:
        return privilege_missing(variable)
    return schema_mismatch()


def migration_failed(revision: str) -> OperatorError:
    """Any failure while a migration ran (SPEC_DECISIONS 79)."""
    return OperatorError(
        f"The migration to {revision} failed and was rolled back. Fix the database state named in the log "
        "and run db migrate again.",
        OPERATION_FAILED,
    )


def not_a_superuser() -> OperatorError:
    """`db provision` with an admin URL whose role cannot create roles and databases."""
    return OperatorError(
        "PG_ADMIN_URL connects as a role that is not a superuser; db provision needs a superuser.",
        CONFIGURATION_ERROR,
    )


def startup_check_refused(check: str, error: BaseException) -> OperatorError:
    """A statement the database refused inside the named startup check (SPEC_DECISIONS 104)."""
    return OperatorError(
        f"The database refused a statement of the startup check {check} "
        f"(SQLSTATE {sqlstate(error) or 'unknown'}); check the settings of the role of DATABASE_URL "
        "and of its database.",
        STARTUP_CHECK_FAILED,
    )
