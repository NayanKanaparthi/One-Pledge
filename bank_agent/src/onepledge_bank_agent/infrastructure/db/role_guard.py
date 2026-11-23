"""The startup guard of every command that connects with `DATABASE_URL` (SPEC_DECISIONS 72, 103).

The role must be least-privilege by the ten points of `role_check.inspect_role`: not a
superuser and no role attribute, member of no role, owner of nothing, CONNECT on its
database and USAGE on `public` only, no column privilege and exactly the privileges of
`GRANT_MATRIX` on every table (PUBLIC included), nothing on any sequence, no setting and no
parameter privilege and a session that fires triggers, no SECURITY DEFINER function and no
EXECUTE outside the system schemas, no default privilege and no grant option. Otherwise the
command refuses with one line naming `DATABASE_URL`, the first violation code and the advice
that fits it. `integrity report` is exempt, so it can show the control that is false.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from onepledge_bank_agent.infrastructure.db.role_check import (
    ADVICE,
    RoleViolation,
    inspect_role,
    owner_problem,
)
from onepledge_bank_agent.infrastructure.operator_errors import CONFIGURATION_ERROR, OperatorError

ROLE_NOT_LEAST_PRIVILEGE = "ROLE_NOT_LEAST_PRIVILEGE"
ROLE_NOT_OWNER = "ROLE_NOT_OWNER"
APP_ROLE_SENTENCE = (
    "Configuration error: DB_APP_ROLE must be {database}_app, the app role of the database of "
    "MIGRATION_DATABASE_URL."
)
_DATABASE_AND_OWNER = text(
    "SELECT current_database(), pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
)


def refusal_sentence(violation: RoleViolation) -> str:
    """The one line of SPEC_DECISIONS 72 and 103: the variable, the first violation code and the advice
    that fits it, never the URL."""
    return f"The role of DATABASE_URL is not least-privilege ({violation.code}); {ADVICE[violation.code]}."


class RuntimeRoleGuard:
    """Checks the role a runtime command connects as."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def violations(self) -> tuple[RoleViolation, ...]:
        """Every way the connected role differs from the app role of the grant matrix."""
        with self._engine.connect() as connection:
            return inspect_role(connection)

    def require_least_privilege(self) -> None:
        """Refuse to start with any other role."""
        found = self.violations()
        if found:
            raise OperatorError(refusal_sentence(found[0]), ROLE_NOT_LEAST_PRIVILEGE)


class OwnerRoleGuard:
    """The mirror check of the administrative commands: `db migrate` and `seed-reference` need the owner."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def require_owner(self) -> None:
        """Refuse when the role of `MIGRATION_DATABASE_URL` does not own the database or a table."""
        with self._engine.connect() as connection:
            problem = owner_problem(connection)
        if problem is not None:
            raise OperatorError(problem, ROLE_NOT_OWNER)

    def require_app_role(self, app_role: str) -> None:
        """`db migrate` grants to DB_APP_ROLE only when it is `{database}_app` of this database and does not
        own it (SPEC_DECISIONS 103)."""
        with self._engine.connect() as connection:
            database, owner = connection.execute(_DATABASE_AND_OWNER).one()
        if app_role != f"{database}_app" or app_role == owner:
            raise OperatorError(APP_ROLE_SENTENCE, CONFIGURATION_ERROR)
