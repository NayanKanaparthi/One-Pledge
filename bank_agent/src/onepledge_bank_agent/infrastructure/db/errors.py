"""Turning database connection failures into one operator sentence, without the URL or any value."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy.exc import OperationalError, ProgrammingError

from onepledge_bank_agent.domain.store_errors import StoreUnavailableError
from onepledge_bank_agent.infrastructure.operator_errors import (
    DATABASE_UNAVAILABLE,
    SCHEMA_NOT_CURRENT,
    OperatorError,
)


@contextlib.contextmanager
def database_errors_as_operator_errors() -> Iterator[None]:
    """Report an unreachable database or a missing schema as what to do next."""
    try:
        yield
    except (OperationalError, StoreUnavailableError):
        raise OperatorError(
            "PostgreSQL refused the connection or is not reachable; check it is running and run "
            "db provision.",
            DATABASE_UNAVAILABLE,
        ) from None
    except ProgrammingError:
        raise OperatorError(
            "The bank database is missing tables or privileges; run db migrate.", SCHEMA_NOT_CURRENT
        ) from None
