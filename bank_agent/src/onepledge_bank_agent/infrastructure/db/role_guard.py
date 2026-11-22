"""`RuntimeRoleGuard`: a runtime process refuses to start with a role that could rewrite history
(SPEC_DECISIONS 43.4).

`worker` (and `serve`, when WP4 adds it) connect with `DATABASE_URL` and must hold the app
role's grants and nothing more. Refused, with one line naming `DATABASE_URL`: a superuser;
a role that owns any table of the schema; UPDATE, DELETE or TRUNCATE on any insert-only
table; DELETE or TRUNCATE on any table; CREATE on the schema.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from onepledge_bank_agent.infrastructure.operator_errors import OperatorError

INSERT_ONLY_TABLES = (
    "loans_raw", "audit_log", "cleaning_issues", "submission_items", "local_checks", "loan_tokens",
    "certificates", "checkpoints_seen", "integrity_runs", "registry_messages_quarantined",
)  # fmt: skip
ROLE_NOT_LEAST_PRIVILEGE = "ROLE_NOT_LEAST_PRIVILEGE"
SENTENCE = (
    "DATABASE_URL connects as a role that could change or remove history (a superuser, an owner, or a role "
    "with update, delete, truncate or create rights it must not have); use the app role."
)
FORBIDDEN = text(
    "SELECT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) "
    "OR EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user) "
    "OR has_schema_privilege(current_user, 'public', 'CREATE') "
    "OR EXISTS (SELECT 1 FROM pg_tables t WHERE t.schemaname = 'public' AND ("
    "has_table_privilege(current_user, format('%I.%I', t.schemaname, t.tablename), 'DELETE') "
    "OR has_table_privilege(current_user, format('%I.%I', t.schemaname, t.tablename), 'TRUNCATE') "
    "OR (t.tablename = ANY(:insert_only) "
    "AND has_table_privilege(current_user, format('%I.%I', t.schemaname, t.tablename), 'UPDATE'))))"
)


class RuntimeRoleGuard:
    """Checks the role a runtime process connects as."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def holds_too_much(self) -> bool:
        """True when the role is a superuser, an owner, or holds a privilege the app role must not have."""
        with self._engine.connect() as connection:
            return bool(connection.execute(FORBIDDEN, {"insert_only": list(INSERT_ONLY_TABLES)}).scalar_one())

    def require_least_privilege(self) -> None:
        """Refuse to start with such a role."""
        if self.holds_too_much():
            raise OperatorError(SENTENCE, ROLE_NOT_LEAST_PRIVILEGE)
