"""`SqlDatabaseControlInspector`: which database controls are in force right now (SPEC 12.4 point 6, 15.5).

Reads `pg_trigger`, `pg_class`, `pg_roles`, `pg_database`, `has_table_privilege` and
`alembic_version` as the role this process connects with, which for every runtime
process is the app role. A trigger is enabled when it exists and `tgenabled` is `O`
(origin) or `A` (always).
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Engine, text

from onepledge_bank_agent.domain.integrity import DatabaseControl

INSERT_ONLY_EVIDENCE = (
    "cleaning_issues", "submission_items", "local_checks", "loan_tokens", "certificates", "checkpoints_seen",
    "integrity_runs",
)  # fmt: skip
ENABLED = ("O", "A")
TRIGGERS = text(
    "SELECT t.tgname, t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
    "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace"
)
PRIVILEGES = text(
    "SELECT has_table_privilege(current_user, 'public.loans_raw', 'UPDATE'), "
    "has_table_privilege(current_user, 'public.loans_raw', 'DELETE'), "
    "has_table_privilege(current_user, 'public.audit_log', 'UPDATE'), "
    "has_table_privilege(current_user, 'public.audit_log', 'DELETE')"
)
NOT_OWNER = text(
    "SELECT NOT r.rolsuper "
    "AND NOT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user) "
    "AND (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()) "
    "<> current_user "
    "FROM pg_roles r WHERE r.rolname = current_user"
)
REVISION = text(
    "SELECT CASE WHEN to_regclass('public.alembic_version') IS NULL THEN NULL "
    "ELSE (SELECT version_num FROM alembic_version LIMIT 1) END"
)


class SqlDatabaseControlInspector:
    """The eleven controls of SPEC 15.5, each true when it holds now."""

    def __init__(self, engine: Engine, head_revision: str) -> None:
        self._engine = engine
        self._head = head_revision

    def controls(self) -> Mapping[DatabaseControl, bool]:
        """Every control and whether it holds."""
        with self._engine.connect() as connection:
            triggers = {str(name): str(state) for name, state in connection.execute(TRIGGERS)}
            update_raw, delete_raw, update_audit, delete_audit = connection.execute(PRIVILEGES).one()
            not_owner = bool(connection.execute(NOT_OWNER).scalar_one_or_none())
            revision = connection.execute(REVISION).scalar_one_or_none()

        def enabled(*names: str) -> bool:
            return all(triggers.get(name) in ENABLED for name in names)

        return {
            DatabaseControl.LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED: enabled("loans_raw_insert_only"),
            DatabaseControl.AUDIT_LOG_IMMUTABILITY_TRIGGER_ENABLED: enabled("audit_log_insert_only"),
            DatabaseControl.EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED: enabled(
                *(f"{table}_insert_only" for table in INSERT_ONLY_EVIDENCE)
            ),
            DatabaseControl.LOAN_STATE_GUARD_TRIGGER_ENABLED: enabled("loans_guard"),
            DatabaseControl.AUDIT_CHAIN_GUARD_TRIGGER_ENABLED: enabled("audit_log_chain_guard"),
            DatabaseControl.APP_ROLE_CANNOT_UPDATE_LOANS_RAW: not update_raw,
            DatabaseControl.APP_ROLE_CANNOT_DELETE_LOANS_RAW: not delete_raw,
            DatabaseControl.APP_ROLE_CANNOT_UPDATE_AUDIT_LOG: not update_audit,
            DatabaseControl.APP_ROLE_CANNOT_DELETE_AUDIT_LOG: not delete_audit,
            DatabaseControl.APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER: not_owner,
            DatabaseControl.SCHEMA_AT_MIGRATION_HEAD: revision == self._head,
        }
