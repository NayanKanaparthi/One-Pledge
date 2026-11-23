"""The least-privilege check of SPEC_DECISIONS 72 on real PostgreSQL: six points, the guard, the mirror.

Each case changes a freshly provisioned and migrated database by hand, as an operator could,
and shows that `inspect_role` reports it with its code and object and that `serve` refuses
with one line naming DATABASE_URL and the first code. The administrative commands refuse a
MIGRATION_DATABASE_URL role that is not the owner of the tables.
"""

from __future__ import annotations

import io
import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from onepledge_outside_records.infrastructure.db.engines import make_engine
from onepledge_outside_records.infrastructure.db.migrations import MigrationRunner
from onepledge_outside_records.infrastructure.db.repositories import SqlRecordsReader
from onepledge_outside_records.infrastructure.db.role_check import (
    RoleViolation,
    Violation,
    inspect_role,
)
from onepledge_outside_records.interface.cli import main
from onepledge_outside_records.interface.output import Console
from onepledge_testtools.command_lines import UnwrappingStream

if TYPE_CHECKING:
    from conftest import Database

# A guard that stopped refusing would start serving, so every test here has a limit (decision 76).
pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]

VARIABLES = ("PG_ADMIN_URL", "DATABASE_URL", "MIGRATION_DATABASE_URL", "DB_APP_ROLE", "PORT", "BIND_HOST")
OWNER_REFUSAL = "The role of MIGRATION_DATABASE_URL is not the owner of the database; use the owner role.\n"
FOREIGN_TABLE = "A table in the schema is owned by another role; the schema was changed outside db migrate.\n"
# SPEC_DECISIONS 103: the advice that fits each code, from one table.
AS_PROVISIONED = "use the app role as provisioned"
NOT_MIGRATED = "the database was not provisioned or migrated correctly; run db migrate as the owner"
SETTINGS = "remove the role's settings"
ADVICE = {
    **dict.fromkeys(
        (
            Violation.ROLE_IS_SUPERUSER,
            Violation.ROLE_HAS_ATTRIBUTE,
            Violation.ROLE_HAS_MEMBERSHIP,
            Violation.ROLE_OWNS_OBJECT,
            Violation.ROLE_HAS_DATABASE_PRIVILEGE,
            Violation.ROLE_HAS_SCHEMA_PRIVILEGE,
            Violation.COLUMN_PRIVILEGE,
            Violation.TABLE_PRIVILEGE_EXTRA,
            Violation.SEQUENCE_PRIVILEGE,
            Violation.SECURITY_DEFINER_FUNCTION,
            Violation.FUNCTION_PRIVILEGE,
            Violation.DEFAULT_PRIVILEGE,
            Violation.GRANT_OPTION,
        ),
        AS_PROVISIONED,
    ),
    Violation.TABLE_PRIVILEGE_MISSING: NOT_MIGRATED,
    Violation.TABLE_NOT_IN_MATRIX: NOT_MIGRATED,
    Violation.ROLE_HAS_SETTING: SETTINGS,
    Violation.PARAMETER_PRIVILEGE: SETTINGS,
    Violation.TRIGGERS_DISABLED_FOR_SESSION: SETTINGS,
    Violation.DATABASE_HAS_SETTING: "remove the database setting",
}


@pytest.fixture
def migrated(provisioned_database: Database) -> Database:
    """A provisioned and migrated database of this test alone."""
    MigrationRunner(provisioned_database.owner_url, provisioned_database.app_role).upgrade()
    return provisioned_database


@pytest.fixture
def admin(migrated: Database) -> Iterator[psycopg.Connection[Any]]:
    """A superuser connection on the test's database."""
    with psycopg.connect(migrated.admin_url, autocommit=True, dbname=migrated.target.database) as connection:
        yield connection


def violations(database: Database) -> tuple[RoleViolation, ...]:
    """What the service's own engine (with its session parameters) finds."""
    engine = make_engine(database.app_url, service=True)
    try:
        with engine.connect() as connection:
            return inspect_role(connection)
    finally:
        engine.dispose()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> Callable[..., tuple[int, str, str]]:
    """`cli(argv, **environment)` runs the command line in process: code, stdout, stderr."""

    def invoke(argv: list[str], **environment: str) -> tuple[int, str, str]:
        for name in VARIABLES:
            monkeypatch.delenv(name, raising=False)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        out, err = io.StringIO(), UnwrappingStream()
        code = main(argv, Console(out, err))
        return code, out.getvalue(), err.getvalue()

    return invoke


def test_the_migrated_app_role_is_least_privilege_and_serve_accepts_it(migrated: Database) -> None:
    assert violations(migrated) == ()
    engine = make_engine(migrated.app_url)
    try:
        SqlRecordsReader(engine).ensure_read_only()
    finally:
        engine.dispose()


@dataclass(frozen=True)
class Case:
    """A hand-made violation: the statements that make it, its first code and the one it must show."""

    name: str
    statements: tuple[str, ...]
    first: Violation
    shown: str


CASES = (
    # 1. Attributes.
    Case("superuser", ("ALTER ROLE {app} SUPERUSER",), Violation.ROLE_IS_SUPERUSER, "current_user"),
    Case("createdb", ("ALTER ROLE {app} CREATEDB",), Violation.ROLE_HAS_ATTRIBUTE, "CREATEDB"),
    Case("createrole", ("ALTER ROLE {app} CREATEROLE",), Violation.ROLE_HAS_ATTRIBUTE, "CREATEROLE"),
    Case("replication", ("ALTER ROLE {app} REPLICATION",), Violation.ROLE_HAS_ATTRIBUTE, "REPLICATION"),
    Case("bypassrls", ("ALTER ROLE {app} BYPASSRLS",), Violation.ROLE_HAS_ATTRIBUTE, "BYPASSRLS"),
    # 2. Membership, of a predefined role or of the owner.
    Case(
        "write_all_data",
        ("GRANT pg_write_all_data TO {app}",),
        Violation.ROLE_HAS_MEMBERSHIP,
        "pg_write_all_data",
    ),
    Case(
        "read_all_data",
        ("GRANT pg_read_all_data TO {app}",),
        Violation.ROLE_HAS_MEMBERSHIP,
        "pg_read_all_data",
    ),
    Case("owner_membership", ("GRANT {owner} TO {app}",), Violation.ROLE_HAS_MEMBERSHIP, "{owner_name}"),
    # 3. Ownership.
    Case(
        "owns_function",
        (
            "CREATE FUNCTION public.optest_f() RETURNS integer LANGUAGE sql AS 'SELECT 1'",
            "ALTER FUNCTION public.optest_f() OWNER TO {app}",
        ),
        Violation.ROLE_OWNS_OBJECT,
        "function public.optest_f",
    ),
    Case(
        "owns_table",
        ("ALTER TABLE public.payments OWNER TO {app}",),
        Violation.ROLE_OWNS_OBJECT,
        "relation public.payments",
    ),
    # 4. Database and schemas.
    Case(
        "database_create",
        ("GRANT CREATE ON DATABASE {db} TO {app}",),
        Violation.ROLE_HAS_DATABASE_PRIVILEGE,
        "{db_name} CREATE",
    ),
    Case(
        "database_temporary",
        ("GRANT TEMPORARY ON DATABASE {db} TO {app}",),
        Violation.ROLE_HAS_DATABASE_PRIVILEGE,
        "{db_name} TEMPORARY",
    ),
    Case(
        "public_create",
        ("GRANT CREATE ON SCHEMA public TO {app}",),
        Violation.ROLE_HAS_SCHEMA_PRIVILEGE,
        "public CREATE",
    ),
    Case(
        "other_schema",
        ("CREATE SCHEMA optest_other", "GRANT USAGE ON SCHEMA optest_other TO {app}"),
        Violation.ROLE_HAS_SCHEMA_PRIVILEGE,
        "optest_other USAGE",
    ),
    # 5. Tables: table and column level, to the role and to PUBLIC, extra and missing.
    Case(
        "table_insert",
        ("GRANT INSERT ON public.payments TO {app}",),
        Violation.TABLE_PRIVILEGE_EXTRA,
        "public.payments INSERT",
    ),
    Case(
        "column_insert",
        (
            "GRANT INSERT (payment_id, vin, paid_on, amount_cents, deposit_bank_code) "
            "ON public.payments TO {app}",
        ),
        Violation.COLUMN_PRIVILEGE,
        "public.payments.vin INSERT",
    ),
    Case(
        "column_update",
        ("GRANT UPDATE (lienholder_name) ON public.title_records TO {app}",),
        Violation.COLUMN_PRIVILEGE,
        "public.title_records.lienholder_name UPDATE",
    ),
    Case(
        "column_references",
        ("GRANT REFERENCES (vin) ON public.contracts TO {app}",),
        Violation.COLUMN_PRIVILEGE,
        "public.contracts.vin REFERENCES",
    ),
    # SPEC_DECISIONS 103.5: SELECT held on one column only is a column privilege and a missing one.
    Case(
        "column_select_only",
        ("REVOKE SELECT ON public.contracts FROM {app}", "GRANT SELECT (vin) ON public.contracts TO {app}"),
        Violation.COLUMN_PRIVILEGE,
        "public.contracts.vin SELECT",
    ),
    Case(
        "column_select_to_public",
        ("GRANT SELECT (api_key_sha256) ON public.api_clients TO PUBLIC",),
        Violation.COLUMN_PRIVILEGE,
        "public.api_clients.api_key_sha256 SELECT",
    ),
    Case(
        "table_delete",
        ("GRANT DELETE ON public.contracts TO {app}",),
        Violation.TABLE_PRIVILEGE_EXTRA,
        "public.contracts DELETE",
    ),
    Case(
        "table_truncate",
        ("GRANT TRUNCATE ON public.payments TO {app}",),
        Violation.TABLE_PRIVILEGE_EXTRA,
        "public.payments TRUNCATE",
    ),
    Case(
        "table_trigger",
        ("GRANT TRIGGER ON public.api_clients TO {app}",),
        Violation.TABLE_PRIVILEGE_EXTRA,
        "public.api_clients TRIGGER",
    ),
    Case(
        "public_grant",
        ("GRANT INSERT ON public.payments TO PUBLIC",),
        Violation.TABLE_PRIVILEGE_EXTRA,
        "public.payments INSERT",
    ),
    Case(
        "alembic_version",
        ("GRANT SELECT ON public.alembic_version TO {app}",),
        Violation.TABLE_PRIVILEGE_EXTRA,
        "public.alembic_version SELECT",
    ),
    Case(
        "missing_grant",
        ("REVOKE SELECT ON public.contracts FROM {app}",),
        Violation.TABLE_PRIVILEGE_MISSING,
        "public.contracts SELECT",
    ),
    Case(
        "table_not_in_matrix",
        ("CREATE TABLE public.optest_extra (x integer)",),
        Violation.TABLE_NOT_IN_MATRIX,
        "public.optest_extra",
    ),
    Case(
        "view_not_in_matrix",
        ("CREATE VIEW public.optest_view AS SELECT 1 AS x",),
        Violation.TABLE_NOT_IN_MATRIX,
        "public.optest_view",
    ),
    # A search path that puts an empty schema first does not hide a grant on public.
    Case(
        "search_path",
        (
            "CREATE SCHEMA optest_decoy",
            "ALTER ROLE {app} SET search_path = optest_decoy, public",
            "GRANT INSERT ON public.payments TO {app}",
        ),
        Violation.TABLE_PRIVILEGE_EXTRA,
        "public.payments INSERT",
    ),
    # 6. Sequences.
    Case(
        "sequence",
        ("CREATE SEQUENCE public.optest_sequence", "GRANT USAGE ON SEQUENCE public.optest_sequence TO {app}"),
        Violation.SEQUENCE_PRIVILEGE,
        "public.optest_sequence",
    ),
    # 7. Session and role settings (SPEC_DECISIONS 103).
    Case(
        "replica_in_database",
        ("ALTER ROLE {app} IN DATABASE {db} SET session_replication_role = replica",),
        Violation.ROLE_HAS_SETTING,
        "in database {db_name}",
    ),
    Case(
        "statement_timeout_setting",
        ("ALTER ROLE {app} SET statement_timeout = '1ms'",),
        Violation.ROLE_HAS_SETTING,
        "in every database",
    ),
    Case(
        "search_path_setting",
        ("ALTER ROLE {app} SET search_path = pg_temp, public",),
        Violation.ROLE_HAS_SETTING,
        "in every database",
    ),
    Case(
        "parameter_privilege",
        ("GRANT SET ON PARAMETER session_replication_role TO {app}",),
        Violation.PARAMETER_PRIVILEGE,
        "session_replication_role",
    ),
    Case(
        "replica_for_the_database",
        ("ALTER DATABASE {db} SET session_replication_role = replica",),
        Violation.DATABASE_HAS_SETTING,
        "database {db_name}: session_replication_role",
    ),
    Case(
        "replica_for_every_role_in_the_database",
        ("ALTER ROLE ALL IN DATABASE {db} SET session_replication_role = local",),
        Violation.DATABASE_HAS_SETTING,
        "database {db_name}: session_replication_role",
    ),
    # 8. Functions.
    Case(
        "security_definer_for_public",
        (
            "CREATE FUNCTION public.optest_definer() RETURNS integer LANGUAGE sql SECURITY DEFINER "
            "AS 'SELECT 1'",
        ),
        Violation.SECURITY_DEFINER_FUNCTION,
        "public.optest_definer",
    ),
    Case(
        "function_executable_by_public",
        ("CREATE FUNCTION public.optest_plain() RETURNS integer LANGUAGE sql AS 'SELECT 1'",),
        Violation.FUNCTION_PRIVILEGE,
        "public.optest_plain",
    ),
    # 9. Default privileges.
    Case(
        "default_privileges",
        ("ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public GRANT INSERT ON TABLES TO {app}",),
        Violation.DEFAULT_PRIVILEGE,
        "{owner_name} tables",
    ),
    # 10. Grant options.
    Case(
        "grant_option",
        ("GRANT SELECT ON public.payments TO {app} WITH GRANT OPTION",),
        Violation.GRANT_OPTION,
        "table public.payments SELECT",
    ),
)


def apply(admin: psycopg.Connection[Any], database: Database, case: Case) -> str:
    """Run the case's statements; return the object it must show, with names filled in."""
    names = {
        "app": sql.Identifier(database.app_role),
        "owner": sql.Identifier(database.target.owner_role),
        "db": sql.Identifier(database.target.database),
    }
    for statement in case.statements:
        admin.execute(
            sql.SQL(statement).format(**{k: v for k, v in names.items() if "{" + k + "}" in statement})
        )
    return case.shown.format(owner_name=database.target.owner_role, db_name=database.target.database)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_every_hand_made_violation_is_found_and_serve_refuses_with_the_first_code(
    migrated: Database,
    admin: psycopg.Connection[Any],
    cli: Callable[..., tuple[int, str, str]],
    case: Case,
) -> None:
    shown = apply(admin, migrated, case)
    found = violations(migrated)
    assert found, case.name
    assert found[0].code == case.first
    assert RoleViolation(case.first, shown) in found, found
    code, out, err = cli(["serve"], **migrated.environment(PORT=str(free_port())))
    assert (code, out) == (1, "")
    assert err == f"The role of DATABASE_URL is not least-privilege ({case.first}); {ADVICE[case.first]}.\n"


def test_every_code_has_its_advice() -> None:
    assert set(ADVICE) == set(Violation)


@pytest.mark.parametrize("name", ["replica_in_database", "replica_for_the_database"])
def test_a_setting_that_explains_the_replica_session_is_the_only_code(
    migrated: Database, admin: psycopg.Connection[Any], name: str
) -> None:
    """SPEC_DECISIONS 128: the session code is only for a replica session no setting explains."""
    apply(admin, migrated, next(case for case in CASES if case.name == name))
    codes = {violation.code for violation in violations(migrated)}
    assert Violation.TRIGGERS_DISABLED_FOR_SESSION not in codes


def test_a_replica_session_no_setting_explains_is_the_session_code(migrated: Database) -> None:
    # Only a superuser may set the parameter in its own session; that is the unexplained case.
    url = str(make_url(migrated.admin_url).set(database=migrated.target.database))
    engine = make_engine(url, service=True)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET session_replication_role = replica")
            found = inspect_role(connection)
    finally:
        engine.dispose()
    assert RoleViolation(Violation.TRIGGERS_DISABLED_FOR_SESSION, "session") in found
    assert not {Violation.DATABASE_HAS_SETTING, Violation.ROLE_HAS_SETTING} & {v.code for v in found}


def test_a_column_grant_on_one_table_is_reported_on_that_table_only(
    migrated: Database, admin: psycopg.Connection[Any]
) -> None:
    app = sql.Identifier(migrated.app_role)
    admin.execute(sql.SQL("GRANT UPDATE (lienholder_name) ON public.title_records TO {}").format(app))
    assert violations(migrated) == (
        RoleViolation(Violation.COLUMN_PRIVILEGE, "public.title_records.lienholder_name UPDATE"),
    )


# The mirror check: the administrative commands run only as the owner of the tables.


def test_seeding_with_the_app_role_says_it_is_not_the_owner(
    migrated: Database,
    cli: Callable[..., tuple[int, str, str]],
    copy_records: Callable[[Path], Path],
    tmp_path: Path,
) -> None:
    data = copy_records(tmp_path)
    env = migrated.environment(MIGRATION_DATABASE_URL=migrated.app_url)
    assert cli(["seed", "--data-dir", str(data)], **env) == (1, "", OWNER_REFUSAL)


def test_adding_a_client_with_the_app_role_says_it_is_not_the_owner(
    migrated: Database, cli: Callable[..., tuple[int, str, str]], make_key: Callable[[], str]
) -> None:
    env = migrated.environment(MIGRATION_DATABASE_URL=migrated.app_url, OUTSIDE_ROLE_TEST_KEY=make_key())
    argv = ["clients", "add", "--name", "x", "--key-env", "OUTSIDE_ROLE_TEST_KEY"]
    assert cli(argv, **env) == (1, "", OWNER_REFUSAL)


def test_migrating_with_the_app_role_says_it_is_not_the_owner_before_and_after_the_first_migration(
    provisioned_database: Database, cli: Callable[..., tuple[int, str, str]]
) -> None:
    env = provisioned_database.environment(MIGRATION_DATABASE_URL=provisioned_database.app_url)
    assert cli(["db", "migrate"], **env) == (1, "", OWNER_REFUSAL)
    MigrationRunner(provisioned_database.owner_url, provisioned_database.app_role).upgrade()
    assert cli(["db", "migrate"], **env) == (1, "", OWNER_REFUSAL)


def test_a_table_the_owner_does_not_own_stops_the_administrative_commands(
    migrated: Database, admin: psycopg.Connection[Any], cli: Callable[..., tuple[int, str, str]]
) -> None:
    """WP1 review 4 quality 5: the owner is the owner; the table is foreign, and that is said."""
    admin.execute("CREATE TABLE public.optest_foreign (x integer)")
    assert cli(["db", "migrate"], **migrated.environment()) == (1, "", FOREIGN_TABLE)


@pytest.mark.parametrize("role", ["{db}_owner", "{db}_other", "postgres"])
def test_migrate_grants_only_to_the_app_role_of_its_database(
    provisioned_database: Database, cli: Callable[..., tuple[int, str, str]], role: str
) -> None:
    """WP1 review 4 quality 4: DB_APP_ROLE set to the owner granted nothing and recorded 0002."""
    env = provisioned_database.environment(DB_APP_ROLE=role.format(db=provisioned_database.target.database))
    assert cli(["db", "migrate"], **env) == (
        1,
        "",
        "Configuration error: DB_APP_ROLE must be {database}_app, the app role of the database of "
        "MIGRATION_DATABASE_URL.\n",
    )
    engine = make_engine(provisioned_database.owner_url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT to_regclass('public.alembic_version')").scalar() is None
    finally:
        engine.dispose()
