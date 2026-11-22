"""The startup guard of every command that connects with `DATABASE_URL` (SPEC_DECISIONS 72).

The role must be least-privilege by the six points of `role_check.inspect_role`: not a
superuser and no role attribute, member of no role, owner of nothing, CONNECT on its
database and USAGE on `public` only, exactly the privileges of `GRANT_MATRIX` on every
table (table and column level, PUBLIC included), and nothing on any sequence. Otherwise
the command refuses with one line naming `DATABASE_URL` and the first violation.
`integrity report` is exempt, so it can show the control that is false.
"""

from __future__ import annotations

from sqlalchemy import Engine

from onepledge_bank_agent.infrastructure.db.role_check import RoleViolation, inspect_role, owns_schema
from onepledge_bank_agent.infrastructure.operator_errors import OperatorError

ROLE_NOT_LEAST_PRIVILEGE = "ROLE_NOT_LEAST_PRIVILEGE"
ROLE_NOT_OWNER = "ROLE_NOT_OWNER"
OWNER_SENTENCE = (
    "MIGRATION_DATABASE_URL connects as a role that does not own the bank tables; use the owner role."
)


def refusal_sentence(violation: RoleViolation) -> str:
    """The one line: the variable and the first violation code, never the URL."""
    return (
        f"DATABASE_URL connects as a role that is not least-privilege ({violation.code}); use the app role."
    )


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
        """Refuse when the role of `MIGRATION_DATABASE_URL` does not own the schema's tables."""
        with self._engine.connect() as connection:
            owner = owns_schema(connection)
        if not owner:
            raise OperatorError(OWNER_SENTENCE, ROLE_NOT_OWNER)
