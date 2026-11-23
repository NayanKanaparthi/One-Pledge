"""`SqlDatabaseControlInspector`: which database controls are in force right now (SPEC 12.4 point 6, 15.5).

Reads `pg_trigger`, `pg_proc` and `alembic_version` as the role this process connects with,
which for every runtime process is the app role. A trigger control is true only when each
trigger it covers is installed exactly as the guard manifest says (`guard_manifest.py`:
table, `tgtype`, function and the hash of its source) and enabled (SPEC_DECISIONS 90). The
five role controls come from the one inspection of
SPEC_DECISIONS 72 (`role_check.inspect_role`), table and column level, so the report sees
exactly what the startup guard sees. `SCHEMA_MATCHES_MANIFEST` compares the whole catalogue of
the schema with the schema manifest (`schema_manifest.py`, SPEC_DECISIONS 105); the objects
that differ are listed with the violations.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Engine, text

from onepledge_bank_agent.domain.integrity import DatabaseControl
from onepledge_bank_agent.infrastructure.db.guard_manifest import guards_named, in_force, installed_triggers
from onepledge_bank_agent.infrastructure.db.role_check import SCHEMA, Violation, inspect_role
from onepledge_bank_agent.infrastructure.db.schema_manifest import catalogue, differences, digest, expected

INSERT_ONLY_EVIDENCE = (
    "cleaning_issues", "submission_items", "local_checks", "loan_tokens", "certificates", "checkpoints_seen",
    "integrity_runs", "registry_messages_quarantined",
)  # fmt: skip
IDENTITY = frozenset(
    {
        Violation.ROLE_IS_SUPERUSER,
        Violation.ROLE_HAS_ATTRIBUTE,
        Violation.ROLE_HAS_MEMBERSHIP,
        Violation.ROLE_OWNS_OBJECT,
    }
)
"""Points 1 to 3 of SPEC_DECISIONS 72: what `APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER` requires."""
READABLE_REVISION = text(
    "SELECT to_regclass('public.alembic_version') IS NOT NULL "
    "AND has_table_privilege('public.alembic_version', 'SELECT')"
)
"""Whether the connected role can read the revision at all: a missing table or a revoked SELECT leaves
`SCHEMA_AT_MIGRATION_HEAD` false (it cannot be shown to hold) instead of failing the whole report; the
revoked grant itself is a `TABLE_PRIVILEGE_MISSING` of `APP_ROLE_GRANTS_EXACT`."""
REVISION = text("SELECT version_num FROM alembic_version LIMIT 1")


class SqlDatabaseControlInspector:
    """The controls of SPEC 15.5 and SPEC_DECISIONS 47, 72 and 105, each true when it holds now."""

    def __init__(self, engine: Engine, head_revision: str) -> None:
        self._engine = engine
        self._head = head_revision

    def controls(self) -> Mapping[DatabaseControl, bool]:
        """Every control and whether it holds; the role controls come from `inspect_role`."""
        with self._engine.connect() as connection:
            triggers = installed_triggers(connection)
            violations = inspect_role(connection)
            readable = bool(connection.execute(READABLE_REVISION).scalar_one())
            live = catalogue(connection)
            revision = connection.execute(REVISION).scalar_one_or_none() if readable else None

        def enabled(*names: str) -> bool:
            return in_force(triggers, guards_named(*names))

        def holds(table: str, privilege: str) -> bool:
            extra = f"{SCHEMA}.{table} {privilege}"
            return not any(
                v.code is Violation.TABLE_PRIVILEGE_EXTRA and v.object_name == extra for v in violations
            )

        return {
            DatabaseControl.LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED: enabled("loans_raw_insert_only"),
            DatabaseControl.AUDIT_LOG_IMMUTABILITY_TRIGGER_ENABLED: enabled("audit_log_insert_only"),
            DatabaseControl.EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED: enabled(
                *(f"{table}_insert_only" for table in INSERT_ONLY_EVIDENCE)
            ),
            DatabaseControl.LOAN_STATE_GUARD_TRIGGER_ENABLED: enabled(
                "loans_guard", "loans_insert_guard", "tape_submissions_guard", "tape_submissions_insert_guard"
            ),
            DatabaseControl.ALERT_STATE_GUARD_TRIGGER_ENABLED: enabled("alerts_guard"),
            DatabaseControl.AUDIT_CHAIN_GUARD_TRIGGER_ENABLED: enabled("audit_log_chain_guard"),
            DatabaseControl.APP_ROLE_CANNOT_UPDATE_LOANS_RAW: holds("loans_raw", "UPDATE"),
            DatabaseControl.APP_ROLE_CANNOT_DELETE_LOANS_RAW: holds("loans_raw", "DELETE"),
            DatabaseControl.APP_ROLE_CANNOT_UPDATE_AUDIT_LOG: holds("audit_log", "UPDATE"),
            DatabaseControl.APP_ROLE_CANNOT_DELETE_AUDIT_LOG: holds("audit_log", "DELETE"),
            DatabaseControl.APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER: not any(
                v.code in IDENTITY for v in violations
            ),
            DatabaseControl.APP_ROLE_GRANTS_EXACT: not violations,
            DatabaseControl.SCHEMA_AT_MIGRATION_HEAD: revision == self._head,
            DatabaseControl.SCHEMA_MATCHES_MANIFEST: digest(live) == digest(expected()),
        }

    def violations(self) -> tuple[str, ...]:
        """Every way the connected role differs from the grant matrix, then every object of the schema that
        differs from the manifest (SPEC_DECISIONS 105), as `CODE on object`."""
        with self._engine.connect() as connection:
            role = tuple(str(violation) for violation in inspect_role(connection))
            live = catalogue(connection)
        wanted = expected()
        schema = tuple(f"{_difference(name, live, wanted)} on {name}" for name in differences(live, wanted))
        return role + schema


def _difference(name: str, live: Mapping[str, str], wanted: Mapping[str, str]) -> str:
    """SCHEMA_OBJECT_ADDED, SCHEMA_OBJECT_MISSING or SCHEMA_OBJECT_CHANGED."""
    if name not in wanted:
        return "SCHEMA_OBJECT_ADDED"
    return "SCHEMA_OBJECT_MISSING" if name not in live else "SCHEMA_OBJECT_CHANGED"
