"""`DatabaseProbe`: the database checks a runtime command runs at startup (SPEC_DECISIONS 103.7, 104).

A connection that does not open is the reachability check's own sentence; the session check
compares the session with the parameters the bank agent sets (`connection_settings.py`). Any
statement the database refuses inside a check reaches the check's boundary
(`interface/cli/startup.py`), which names the check and the SQLSTATE.
"""

from __future__ import annotations

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import OperationalError

from onepledge_bank_agent.infrastructure.db.connection_settings import session_parameter_problems
from onepledge_bank_agent.infrastructure.db.failures import connection_failed
from onepledge_bank_agent.infrastructure.operator_errors import CONFIGURATION_ERROR, OperatorError

VARIABLE = "DATABASE_URL"


class DatabaseProbe:
    """Opens connections of the inspecting engine of `DATABASE_URL` only to check them."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def ensure_reachable(self) -> None:
        """The database accepts a connection and answers one statement."""
        with self._open() as connection:
            connection.execute(text("SELECT 1"))

    def ensure_session_parameters(self) -> None:
        """Every session parameter the bank agent sets holds in the session."""
        with self._open() as connection:
            differing = session_parameter_problems(connection)
        if differing:
            raise OperatorError(
                "The database did not keep the session parameters the bank agent sets "
                f"({', '.join(differing)}); check the database's configuration.",
                CONFIGURATION_ERROR,
            )

    def _open(self) -> Connection:
        try:
            return self._engine.connect()
        except OperationalError as error:
            raise connection_failed(error, VARIABLE) from None
