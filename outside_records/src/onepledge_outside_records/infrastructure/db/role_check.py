"""The one definition of a least-privilege app role, and the owner check (SPEC_DECISIONS 72, 103).

`GRANT_MATRIX` is the only statement of what the app role may hold: the grants migration
issues it, `inspect_role` compares the connected role with it, and a test asserts it equals
SPEC 6.1 (SELECT on the four tables, nothing else). `inspect_role` evaluates the ten points
of decisions 72 and 103 and returns every violation, in point order; `serve` refuses on the
first, with the advice that fits its code (`ADVICE`, one table).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from sqlalchemy import Connection, text

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.db import role_points
from onepledge_outside_records.infrastructure.db.role_points import ordered

SCHEMA = "public"
# Table of the schema to the privileges the app role holds on it (SPEC 6.1 as amended by
# SPEC_DECISIONS 132): SELECT on the four data tables and on `alembic_version`, whose revision
# the schema startup check reads. A table of `public` that is not listed here is a violation.
GRANT_MATRIX: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "title_records": frozenset({"SELECT"}),
        "payments": frozenset({"SELECT"}),
        "contracts": frozenset({"SELECT"}),
        "api_clients": frozenset({"SELECT"}),
        "alembic_version": frozenset({"SELECT"}),
    }
)
# Compared at table level only: a privilege held on a column is COLUMN_PRIVILEGE, never "held"
# (SPEC_DECISIONS 103.5).
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES", "DELETE", "TRUNCATE", "TRIGGER")
ATTRIBUTES = ("CREATEDB", "CREATEROLE", "REPLICATION", "BYPASSRLS")


class Violation(StrEnum):
    """The codes of SPEC_DECISIONS 72 and 103."""

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
    COLUMN_PRIVILEGE = "COLUMN_PRIVILEGE"
    ROLE_HAS_SETTING = "ROLE_HAS_SETTING"
    PARAMETER_PRIVILEGE = "PARAMETER_PRIVILEGE"
    TRIGGERS_DISABLED_FOR_SESSION = "TRIGGERS_DISABLED_FOR_SESSION"
    DATABASE_HAS_SETTING = "DATABASE_HAS_SETTING"
    SECURITY_DEFINER_FUNCTION = "SECURITY_DEFINER_FUNCTION"
    FUNCTION_PRIVILEGE = "FUNCTION_PRIVILEGE"
    DEFAULT_PRIVILEGE = "DEFAULT_PRIVILEGE"
    GRANT_OPTION = "GRANT_OPTION"


_AS_PROVISIONED = "use the app role as provisioned"
_NOT_MIGRATED = "the database was not provisioned or migrated correctly; run db migrate as the owner"
_SETTINGS = "remove the role's settings"
# The advice for each code (SPEC_DECISIONS 103): an attribute, membership, ownership or extra
# privilege; a privilege the migrations should have given; a setting or parameter.
ADVICE: Mapping[Violation, str] = MappingProxyType(
    {
        Violation.ROLE_IS_SUPERUSER: _AS_PROVISIONED,
        Violation.ROLE_HAS_ATTRIBUTE: _AS_PROVISIONED,
        Violation.ROLE_HAS_MEMBERSHIP: _AS_PROVISIONED,
        Violation.ROLE_OWNS_OBJECT: _AS_PROVISIONED,
        Violation.ROLE_HAS_DATABASE_PRIVILEGE: _AS_PROVISIONED,
        Violation.ROLE_HAS_SCHEMA_PRIVILEGE: _AS_PROVISIONED,
        Violation.TABLE_PRIVILEGE_EXTRA: _AS_PROVISIONED,
        Violation.COLUMN_PRIVILEGE: _AS_PROVISIONED,
        Violation.SEQUENCE_PRIVILEGE: _AS_PROVISIONED,
        Violation.SECURITY_DEFINER_FUNCTION: _AS_PROVISIONED,
        Violation.FUNCTION_PRIVILEGE: _AS_PROVISIONED,
        Violation.DEFAULT_PRIVILEGE: _AS_PROVISIONED,
        Violation.GRANT_OPTION: _AS_PROVISIONED,
        Violation.TABLE_PRIVILEGE_MISSING: _NOT_MIGRATED,
        Violation.TABLE_NOT_IN_MATRIX: _NOT_MIGRATED,
        Violation.ROLE_HAS_SETTING: _SETTINGS,
        Violation.PARAMETER_PRIVILEGE: _SETTINGS,
        Violation.TRIGGERS_DISABLED_FOR_SESSION: _SETTINGS,
        Violation.DATABASE_HAS_SETTING: "remove the database setting",
    }
)


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
# One column per privilege, in the order of PRIVILEGES; table level only.
_TABLES = text("""
SELECT c.relname,
    has_table_privilege(current_user, c.oid, 'SELECT'),
    has_table_privilege(current_user, c.oid, 'INSERT'),
    has_table_privilege(current_user, c.oid, 'UPDATE'),
    has_table_privilege(current_user, c.oid, 'REFERENCES'),
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
""")


def inspect_role(connection: Connection) -> tuple[RoleViolation, ...]:
    """Every violation of the ten points of SPEC_DECISIONS 72 and 103 by the connected role, in
    point order."""
    found: list[RoleViolation] = []
    superuser, *attributes = connection.execute(_ATTRIBUTES).one()
    if superuser:
        found.append(RoleViolation(Violation.ROLE_IS_SUPERUSER, "current_user"))
    found.extend(
        RoleViolation(Violation.ROLE_HAS_ATTRIBUTE, name)
        for name, held in zip(ATTRIBUTES, attributes, strict=True)
        if held
    )
    found.extend(RoleViolation(Violation.ROLE_HAS_MEMBERSHIP, n) for n in ordered(connection, _MEMBERSHIPS))
    found.extend(RoleViolation(Violation.ROLE_OWNS_OBJECT, n) for n in ordered(connection, _OWNED))
    found.extend(_database_and_schemas(connection))
    found.extend(_coded(role_points.column_privileges(connection)))
    found.extend(_tables(connection))
    found.extend(RoleViolation(Violation.SEQUENCE_PRIVILEGE, n) for n in ordered(connection, _SEQUENCES))
    for point in (
        role_points.settings,
        role_points.functions,
        role_points.default_privileges,
        role_points.grant_options,
    ):
        found.extend(_coded(point(connection)))
    return tuple(found)


def _coded(pairs: list[tuple[str, str]]) -> list[RoleViolation]:
    return [RoleViolation(Violation(code), object_name) for code, object_name in pairs]


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
    """The one sentence `serve` prints: DATABASE_URL, the first violation code and its advice."""
    first = violations[0].code
    return OperatorError(
        f"The role of DATABASE_URL is not least-privilege ({first}); {ADVICE[first]}.",
        "UNSAFE_DATABASE_ROLE",
    )


_FOREIGN_TABLES = text("""
SELECT c.relname FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relowner <> current_user::regrole
""")
_OWNS_DATABASE = text("""
SELECT EXISTS (
    SELECT 1 FROM pg_database WHERE datname = current_database() AND datdba = current_user::regrole
)
""")


def owner_problem(connection: Connection) -> OperatorError | None:
    """The refusal of an administrative command that does not run as the owner of the schema.

    The role of MIGRATION_DATABASE_URL must own the database, which db provision gives the
    owner role; and every table of the schema must be its own, or the schema was changed
    outside db migrate (SPEC_DECISIONS 72 mirror check, 103).
    """
    owns_database: bool = connection.execute(_OWNS_DATABASE).scalar_one()
    if not owns_database:
        return OperatorError(
            "The role of MIGRATION_DATABASE_URL is not the owner of the database; use the owner role.",
            "CONFIGURATION_ERROR",
        )
    if connection.execute(_FOREIGN_TABLES).first() is not None:
        return OperatorError(
            "A table in the schema is owned by another role; the schema was changed outside db migrate.",
            "CONFIGURATION_ERROR",
        )
    return None
