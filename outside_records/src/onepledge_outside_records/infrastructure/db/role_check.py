"""The one definition of a least-privilege app role, and the owner check (SPEC_DECISIONS 72).

`GRANT_MATRIX` is the only statement of what the app role may hold: the grants migration
issues it, `inspect_role` compares the connected role with it, and a test asserts it equals
SPEC 6.1 (SELECT on the four tables, nothing else). `inspect_role` evaluates the six points
of decision 72 and returns every violation, in point order; `serve` refuses on the first.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from sqlalchemy import Connection, text

from onepledge_outside_records.domain.errors import OperatorError

SCHEMA = "public"
# Table of the schema to the privileges the app role holds on it. A table of `public` that is
# not listed here is a violation; `alembic_version` is listed with no privilege at all.
GRANT_MATRIX: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "title_records": frozenset({"SELECT"}),
        "payments": frozenset({"SELECT"}),
        "contracts": frozenset({"SELECT"}),
        "api_clients": frozenset({"SELECT"}),
        "alembic_version": frozenset(),
    }
)
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


def refusal(violations: tuple[RoleViolation, ...]) -> OperatorError:
    """The one line `serve` prints: DATABASE_URL and the first violation code, within 100 columns."""
    return OperatorError(
        f"The role of DATABASE_URL is not least-privilege ({violations[0].code}); use the app role.",
        "UNSAFE_DATABASE_ROLE",
    )


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


def owner_problem(connection: Connection) -> OperatorError | None:
    """The refusal of an administrative command whose MIGRATION_DATABASE_URL role is not the owner.

    The role must own every table of the schema; on a database with no table yet, it must own
    the database, which db provision gives the owner role (SPEC_DECISIONS 72, mirror check).
    """
    foreign = connection.execute(_FOREIGN_TABLES).first()
    owns_database: bool = connection.execute(_OWNS_DATABASE).scalar_one()
    if foreign is None and owns_database:
        return None
    return OperatorError(
        "The role of MIGRATION_DATABASE_URL does not own the outside records tables; use the owner role.",
        "CONFIGURATION_ERROR",
    )
