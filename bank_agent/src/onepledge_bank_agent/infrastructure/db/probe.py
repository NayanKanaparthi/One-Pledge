"""`DatabaseProbe`: the database checks a runtime command runs at startup (SPEC_DECISIONS 103.7, 104).

A connection that does not open is the reachability check's own sentence; the session check
compares the session with the parameters the bank agent sets (`connection_settings.py`). Any
statement the database refuses inside a check reaches the check's boundary
(`interface/cli/startup.py`), which names the check and the SQLSTATE.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from onepledge_bank_agent.infrastructure.db.connection_settings import session_parameter_problems
from onepledge_bank_agent.infrastructure.db.failures import connection_failed, startup_check_refused
from onepledge_bank_agent.infrastructure.db.role_guard import RuntimeRoleGuard
from onepledge_bank_agent.infrastructure.operator_errors import CONFIGURATION_ERROR, OperatorError

VARIABLE = "DATABASE_URL"
_ENCODING = text(
    "SELECT current_setting('server_encoding') COLLATE \"C\" = 'UTF8', current_setting('server_encoding')"
)


def startup_check[T](name: str, action: Callable[[], T]) -> T:
    """Run one named check; a statement the database refuses is that check's failure."""
    try:
        return action()
    except DBAPIError as error:
        raise startup_check_refused(name, error) from None


class DatabaseProbe:
    """Opens connections of the inspecting engine of `DATABASE_URL` only to check them."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def ensure_reachable(self) -> None:
        """The database accepts a connection and answers one statement."""
        with self._open() as connection:
            connection.execute(text("SELECT 1"))

    def ensure_utf8(self) -> None:
        """The database encoding is UTF8, the one under which "C" orders by code point (SPEC_DECISIONS
        133.1)."""
        with self._open() as connection:
            utf8, shown = connection.execute(_ENCODING).one()
        if not utf8:
            name = shown.decode("ascii", "replace") if isinstance(shown, bytes) else str(shown)
            raise OperatorError(
                f"The database of DATABASE_URL has the encoding {name}, not UTF8; create it again with "
                "db provision, which makes it UTF8.",
                CONFIGURATION_ERROR,
            )

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

    def ensure_least_privilege(self) -> None:
        """The role inspection of SPEC_DECISIONS 103 finds nothing (`RuntimeRoleGuard`)."""
        RuntimeRoleGuard(self._engine).require_least_privilege()

    def _open(self) -> Connection:
        try:
            return self._engine.connect()
        except OperationalError as error:
            raise connection_failed(error, VARIABLE) from None
