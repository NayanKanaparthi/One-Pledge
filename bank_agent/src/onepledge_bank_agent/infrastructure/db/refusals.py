"""The database's refusals as the store errors of the ports (SPEC_DECISIONS 85, 92).

A guard's SQLSTATE (`OP001` to `OP006`), a unique key, and every other integrity or data
rule become `StoreRefusedError`; a lost or timed out connection, a serialisation failure
and a deadlock become `StoreUnavailableError`. Anything else (a missing table, a missing
privilege) is left as it is: it is a database that is not provisioned as the code
expects, which the command line reports with its own sentence.
"""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError, OperationalError

from onepledge_bank_agent.domain.errors import DomainError
from onepledge_bank_agent.domain.store_errors import (
    GUARD_CODES,
    UNIQUE,
    StoreRefusedError,
    StoreUnavailableError,
)

UNIQUE_VIOLATION = "23505"
REFUSAL_CLASSES = ("22", "23")
"""SQLSTATE classes of a value or a constraint the database refused: data exception, integrity."""
PASSING = frozenset({"40001", "40P01", "57014", "57P01", "57P02", "57P03"})
"""Serialisation failure, deadlock, statement timeout and the three shutdown states."""
CONNECTION_CLASS = "08"


def sqlstate_of(error: DBAPIError) -> str | None:
    """The SQLSTATE the driver reported, if any."""
    state = getattr(error.orig, "sqlstate", None)
    return state if isinstance(state, str) else None


def store_error(error: DBAPIError) -> DomainError | None:
    """The store error a driver error stands for, or None when it is neither a refusal nor passing."""
    state = sqlstate_of(error)
    constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    if state in GUARD_CODES:
        return StoreRefusedError(state, state, constraint)
    if state == UNIQUE_VIOLATION:
        return StoreRefusedError(UNIQUE, state, constraint)
    if state is not None and state[:2] in REFUSAL_CLASSES:
        return StoreRefusedError(state, state, constraint)
    if state in PASSING or (state is not None and state[:2] == CONNECTION_CLASS):
        return StoreUnavailableError(state)
    if error.connection_invalidated or (state is None and isinstance(error, OperationalError)):
        return StoreUnavailableError(state)
    return None
