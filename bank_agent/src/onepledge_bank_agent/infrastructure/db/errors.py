"""Turning database failures into one operator sentence naming the real cause (SPEC_DECISIONS 78).

A server that is not there, one that refuses the connection, a database without tables,
a role without a privilege and a schema behind the code each have their own sentence,
naming the variable the command connected with, never its value.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from sqlalchemy.exc import OperationalError, ProgrammingError

from onepledge_bank_agent.domain.store_errors import StoreMismatchError, StoreUnavailableError
from onepledge_bank_agent.infrastructure.db.failures import connection_failed, query_refused, sqlstate


@contextlib.contextmanager
def database_errors_as_operator_errors(variable: str = "DATABASE_URL") -> Iterator[None]:
    """Report a database failure of a command that connects with `variable` as what to do next."""
    try:
        yield
    except OperationalError as error:
        raise connection_failed(error, variable) from None
    except StoreUnavailableError:
        raise connection_failed(None, variable) from None
    except StoreMismatchError as error:
        raise query_refused(error.sqlstate, variable) from None
    except ProgrammingError as error:
        raise query_refused(sqlstate(error), variable) from None
