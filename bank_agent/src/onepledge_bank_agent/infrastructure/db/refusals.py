"""The database's refusals as the store errors of the ports (SPEC_DECISIONS 85, 92).

A guard's SQLSTATE (`OP001` to `OP006`), a unique key, and every other integrity or data
rule become `StoreRefusedError`; a lost or timed out connection, a serialisation failure
and a deadlock become `StoreUnavailableError`; a missing table, column, function or
privilege (class 42) becomes `StoreMismatchError`, a database that is not migrated or
provisioned as the code expects, which the command line reports with its own sentence. A
value the driver cannot decode while reading (no SQLSTATE) becomes `StoreUnreadableError`
(SPEC_DECISIONS 100).
"""

from __future__ import annotations

from sqlalchemy.exc import DataError, DBAPIError, OperationalError

from onepledge_bank_agent.domain.errors import DomainError
from onepledge_bank_agent.domain.store_errors import (
    GUARD_CODES,
    UNIQUE,
    StoreMismatchError,
    StoreRefusedError,
    StoreUnavailableError,
    StoreUnreadableError,
)

UNIQUE_VIOLATION = "23505"
REFUSAL_CLASSES = ("22", "23")
"""SQLSTATE classes of a value or a constraint the database refused: data exception, integrity."""
PASSING = frozenset({"40001", "40P01", "57014", "57P01", "57P02", "57P03"})
"""Serialisation failure, deadlock, statement timeout and the three shutdown states."""
CONNECTION_CLASS = "08"
MISMATCH_CLASS = "42"
"""Syntax error or access rule violation: an undefined table, column or function, a missing privilege."""


def sqlstate_of(error: DBAPIError) -> str | None:
    """The SQLSTATE the driver reported, if any."""
    state = getattr(error.orig, "sqlstate", None)
    return state if isinstance(state, str) else None


def store_error(error: DBAPIError) -> DomainError | None:
    """The store error a driver error stands for, or None when it is neither a refusal nor passing."""
    state = sqlstate_of(error)
    if state is None and isinstance(error, DataError):  # the driver could not decode a stored value
        return StoreUnreadableError(None)
    constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    if state is not None and (state in GUARD_CODES or state[:2] in REFUSAL_CLASSES):
        return StoreRefusedError(UNIQUE if state == UNIQUE_VIOLATION else state, state, constraint)
    if state in PASSING or (state is not None and state[:2] == CONNECTION_CLASS):
        return StoreUnavailableError(state)
    if error.connection_invalidated or (state is None and isinstance(error, OperationalError)):
        return StoreUnavailableError(state)
    if state is not None and state[:2] == MISMATCH_CLASS:
        return StoreMismatchError(state)
    return None
