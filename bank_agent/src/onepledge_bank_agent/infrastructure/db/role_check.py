"""The one definition of a least-privilege app role, and the owner check (SPEC_DECISIONS 72).

`GRANT_MATRIX` is the only statement of what the app role may hold on each table of
`public`: the grants migrations issue it (`apply_grants`), `inspect_role` compares the
connected role with it for the startup guard and for the integrity report, and a test
asserts it equals SPEC 12.2 as amended. `inspect_role` evaluates the six points of
decision 72 and returns every violation, in point order. There is no second query.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from sqlalchemy import Connection, text

SCHEMA = "public"
READ = frozenset({"SELECT"})
APPEND = frozenset({"SELECT", "INSERT"})
WORK = frozenset({"SELECT", "INSERT", "UPDATE"})
# Table of the schema to the privileges the app role holds on it (SPEC 12.2, SPEC_DECISIONS 57,
# 72, 85). A table of `public` that is not listed is a violation; no sequence is granted.
GRANT_MATRIX: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "loans_raw": APPEND, "audit_log": APPEND, "cleaning_issues": APPEND, "submission_items": APPEND,
        "local_checks": APPEND, "loan_tokens": APPEND, "certificates": APPEND, "checkpoints_seen": APPEND,
        "integrity_runs": APPEND, "registry_messages_quarantined": APPEND, "login_attempts": APPEND,
        "tape_submissions": WORK, "loans": WORK, "registry_requests": WORK, "registry_request_items": WORK,
        "alerts": WORK, "users": WORK, "registry_outbox_attempts": WORK,
        "lenders": READ, "facilities": READ, "alembic_version": READ,
    }
)  # fmt: skip

# Privileges that a column grant can give are held when the table or any column has them.
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
TABLE_ONLY_PRIVILEGES = ("DELETE", "TRUNCATE", "TRIGGER")
PRIVILEGES = (*COLUMN_PRIVILEGES, *TABLE_ONLY_PRIVILEGES)
ATTRIBUTES = ("CREATEDB", "CREATEROLE", "REPLICATION", "BYPASSRLS")


class Violation(StrEnum):
    """The codes of SPEC_DECISIONS 72."""

    ROLE_IS_SUPERUSER = "ROLE_IS_SUPERUSER"
    ROLE_HAS_ATTRIBUTE = "ROLE_HAS_ATTRIBUTE"
    ROLE_HAS_MEMBERSHIP = "ROLE_HAS_MEMBERSHIP"
    ROLE_OWNS_OBJECT = "ROLE_OWNS_OBJECT"
    ROLE_HAS_DATABASE_PRIVILEGE = "ROLE_HAS_DATABASE_PRIVILEGE"
    ROLE_HAS_SCHEMA_PRIVILEGE = "ROLE_HAS_SCHEMA_PRIVILEGE"
    TABLE_PRIVILEGE_EXTRA = "TABLE_PRIVILEGE_EXTRA"
    TABLE_PRIVILEGE_MISSING = "TABLE_PRIVILEGE_MISSING"
    TABLE_NOT_IN_MATRIX = "TABLE_NOT_IN_MATRIX"
    SEQUENCE_PRIVILEGE = "SEQUENCE_PRIVILEGE"


@dataclass(frozen=True, order=True)
class RoleViolation:
    """One way the connected role differs from a least-privilege app role, with the object named."""

    code: Violation
    object_name: str

    def __str__(self) -> str:
        return f"{self.code} on {self.object_name}"


# The queries are fixed text: nothing of a request or a setting is ever spliced into them.
# `current_user::regrole` is the connected role; a user schema is any but pg_* and information_schema.
_ATTRIBUTES = text(
    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
    "FROM pg_roles WHERE rolname = current_user"
)
_MEMBERSHIPS = text("""
SELECT r.rolname FROM pg_auth_members AS m JOIN pg_roles AS r ON r.oid = m.roleid
WHERE m.member = current_user::regrole ORDER BY r.rolname
""")
_OWNED = text("""
SELECT 'relation ' || n.nspname || '.' || c.relname FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE c.relowner = current_user::regrole
        AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
UNION ALL SELECT 'function ' || n.nspname || '.' || p.proname FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE p.proowner = current_user::regrole
        AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
UNION ALL SELECT 'type ' || n.nspname || '.' || t.typname FROM pg_type AS t
    JOIN pg_namespace AS n ON n.oid = t.typnamespace
    WHERE t.typowner = current_user::regrole AND t.typrelid = 0 AND t.typelem = 0
        AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
UNION ALL SELECT 'schema ' || n.nspname FROM pg_namespace AS n
    WHERE n.nspowner = current_user::regrole
UNION ALL SELECT 'database ' || d.datname FROM pg_database AS d
    WHERE d.datname = current_database() AND d.datdba = current_user::regrole
ORDER BY 1
""")
_DATABASE = text(
    "SELECT has_database_privilege(current_user, current_database(), 'CREATE'), "
    "has_database_privilege(current_user, current_database(), 'TEMPORARY'), current_database()"
)
_SCHEMAS = text("""
SELECT n.nspname, has_schema_privilege(current_user, n.oid, 'USAGE'),
    has_schema_privilege(current_user, n.oid, 'CREATE')
FROM pg_namespace AS n
WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' ORDER BY n.nspname
""")
# One column per privilege, in the order of COLUMN_PRIVILEGES then TABLE_ONLY_PRIVILEGES.
_TABLES = text("""
SELECT c.relname,
    (has_table_privilege(current_user, c.oid, 'SELECT')
        OR has_any_column_privilege(current_user, c.oid, 'SELECT')),
    (has_table_privilege(current_user, c.oid, 'INSERT')
        OR has_any_column_privilege(current_user, c.oid, 'INSERT')),
    (has_table_privilege(current_user, c.oid, 'UPDATE')
        OR has_any_column_privilege(current_user, c.oid, 'UPDATE')),
    (has_table_privilege(current_user, c.oid, 'REFERENCES')
        OR has_any_column_privilege(current_user, c.oid, 'REFERENCES')),
    has_table_privilege(current_user, c.oid, 'DELETE'),
    has_table_privilege(current_user, c.oid, 'TRUNCATE'),
    has_table_privilege(current_user, c.oid, 'TRIGGER')
FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f') ORDER BY c.relname
""")
_SEQUENCES = text("""
SELECT n.nspname || '.' || c.relname FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind = 'S' AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
    AND (has_sequence_privilege(current_user, c.oid, 'USAGE')
        OR has_sequence_privilege(current_user, c.oid, 'SELECT')
        OR has_sequence_privilege(current_user, c.oid, 'UPDATE'))
ORDER BY 1
""")


def inspect_role(connection: Connection) -> tuple[RoleViolation, ...]:
    """Every violation of the six points of SPEC_DECISIONS 72 by the connected role, in point order."""
    found: list[RoleViolation] = []
    superuser, *attributes = connection.execute(_ATTRIBUTES).one()
    if superuser:
        found.append(RoleViolation(Violation.ROLE_IS_SUPERUSER, "current_user"))
    found.extend(
        RoleViolation(Violation.ROLE_HAS_ATTRIBUTE, name)
        for name, held in zip(ATTRIBUTES, attributes, strict=True)
        if held
    )
    found.extend(RoleViolation(Violation.ROLE_HAS_MEMBERSHIP, r[0]) for r in connection.execute(_MEMBERSHIPS))
    found.extend(RoleViolation(Violation.ROLE_OWNS_OBJECT, r[0]) for r in connection.execute(_OWNED))
    found.extend(_database_and_schemas(connection))
    found.extend(_tables(connection))
    found.extend(RoleViolation(Violation.SEQUENCE_PRIVILEGE, r[0]) for r in connection.execute(_SEQUENCES))
    return tuple(found)


def _database_and_schemas(connection: Connection) -> list[RoleViolation]:
    create, temporary, database = connection.execute(_DATABASE).one()
    found = [
        RoleViolation(Violation.ROLE_HAS_DATABASE_PRIVILEGE, f"{database} {privilege}")
        for privilege, held in (("CREATE", create), ("TEMPORARY", temporary))
        if held
    ]
    for schema, usage, create_in in connection.execute(_SCHEMAS):
        held = [name for name, value in (("USAGE", usage), ("CREATE", create_in)) if value]
        allowed = {"USAGE"} if schema == SCHEMA else set()
        found.extend(
            RoleViolation(Violation.ROLE_HAS_SCHEMA_PRIVILEGE, f"{schema} {name}")
            for name in held
            if name not in allowed
        )
    return found


def _tables(connection: Connection) -> list[RoleViolation]:
    found: list[RoleViolation] = []
    seen: set[str] = set()
    for table, *flags in connection.execute(_TABLES):
        seen.add(table)
        held = {name for name, value in zip(PRIVILEGES, flags, strict=True) if value}
        if table not in GRANT_MATRIX:
            found.append(RoleViolation(Violation.TABLE_NOT_IN_MATRIX, f"{SCHEMA}.{table}"))
            continue
        expected = GRANT_MATRIX[table]
        found.extend(
            RoleViolation(Violation.TABLE_PRIVILEGE_EXTRA, f"{SCHEMA}.{table} {p}")
            for p in sorted(held - expected)
        )
        found.extend(
            RoleViolation(Violation.TABLE_PRIVILEGE_MISSING, f"{SCHEMA}.{table} {p}")
            for p in sorted(expected - held)
        )
    for table in sorted(set(GRANT_MATRIX) - seen):
        found.extend(
            RoleViolation(Violation.TABLE_PRIVILEGE_MISSING, f"{SCHEMA}.{table} {p}")
            for p in sorted(GRANT_MATRIX[table])
        )
    return found


_EXISTING = text("""
SELECT c.relname FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY 1
""")
_OWNED_SEQUENCES = text("""
SELECT c.relname FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'S' ORDER BY 1
""")


def apply_grants(connection: Connection, role: str) -> None:
    """Make the role's privileges on every existing table of the matrix exactly the matrix's, and take
    every sequence privilege away. `role` is a validated role name (the migrations check it)."""
    existing = {row[0] for row in connection.execute(_EXISTING)}
    for table, privileges in GRANT_MATRIX.items():
        if table not in existing:
            continue
        connection.execute(text(f'REVOKE ALL ON TABLE public."{table}" FROM "{role}"'))
        if privileges:
            listed = ", ".join(sorted(privileges))
            connection.execute(text(f'GRANT {listed} ON TABLE public."{table}" TO "{role}"'))
    for (sequence,) in connection.execute(_OWNED_SEQUENCES):
        connection.execute(text(f'REVOKE ALL ON SEQUENCE public."{sequence}" FROM "{role}"'))


_FOREIGN_TABLES = text("""
SELECT c.relname FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relowner <> current_user::regrole
ORDER BY 1
""")
_OWNS_DATABASE = text("""
SELECT EXISTS (
    SELECT 1 FROM pg_database WHERE datname = current_database() AND datdba = current_user::regrole
)
""")


def owns_schema(connection: Connection) -> bool:
    """The connected role owns every table of `public` and the database (the mirror check of decision 72)."""
    foreign = connection.execute(_FOREIGN_TABLES).first()
    owns_database: bool = connection.execute(_OWNS_DATABASE).scalar_one()
    return foreign is None and owns_database
