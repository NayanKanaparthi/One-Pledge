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

if TYPE_CHECKING:
    from conftest import Database

# A guard that stopped refusing would start serving, so every test here has a limit (decision 76).
pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]

VARIABLES = ("PG_ADMIN_URL", "DATABASE_URL", "MIGRATION_DATABASE_URL", "DB_APP_ROLE", "PORT", "BIND_HOST")
OWNER_REFUSAL = (
    "The role of MIGRATION_DATABASE_URL does not own the outside records tables; "
    "use the owner role created by db provision.\n"
)


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
    engine = make_engine(database.app_url)
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
        out, err = io.StringIO(), io.StringIO()
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
        Violation.TABLE_PRIVILEGE_EXTRA,
        "public.payments INSERT",
    ),
    Case(
        "column_update",
        ("GRANT UPDATE (lienholder_name) ON public.title_records TO {app}",),
        Violation.TABLE_PRIVILEGE_EXTRA,
        "public.title_records UPDATE",
    ),
    Case(
        "column_references",
        ("GRANT REFERENCES (vin) ON public.contracts TO {app}",),
        Violation.TABLE_PRIVILEGE_EXTRA,
        "public.contracts REFERENCES",
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
    assert err == f"The role of DATABASE_URL is not least-privilege ({case.first}); use the app role.\n"


def test_a_column_grant_on_one_table_is_reported_on_that_table_only(
    migrated: Database, admin: psycopg.Connection[Any]
) -> None:
    app = sql.Identifier(migrated.app_role)
    admin.execute(sql.SQL("GRANT UPDATE (lienholder_name) ON public.title_records TO {}").format(app))
    assert violations(migrated) == (
        RoleViolation(Violation.TABLE_PRIVILEGE_EXTRA, "public.title_records UPDATE"),
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
    admin.execute("CREATE TABLE public.optest_foreign (x integer)")
    assert cli(["db", "migrate"], **migrated.environment()) == (1, "", OWNER_REFUSAL)
