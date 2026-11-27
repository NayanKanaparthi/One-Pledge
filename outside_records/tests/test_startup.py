"""`serve` starts through named checks, and none ends in the last resort (SPEC_DECISIONS 103, 104).

The three reproductions of WP1 review 4, failure 2, each answered by its own sentence; a
statement the database refuses inside a check, named by the check and its SQLSTATE; the
session parameters the service sets on every connection whatever the role's defaults; and a
pooled connection whose session would not fire triggers, refused.
"""

from __future__ import annotations

import io
import socket
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.exc import OperationalError, ProgrammingError

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.db.engines import make_engine
from onepledge_outside_records.infrastructure.db.migrations import MigrationRunner, installed_head
from onepledge_outside_records.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_outside_records.infrastructure.db.session import SESSION_PARAMETERS, refuse_replica_sessions
from onepledge_outside_records.interface.cli import main
from onepledge_outside_records.interface.output import UNEXPECTED_ERROR, Console
from onepledge_outside_records.interface.startup import CHECK_NAMES, startup_check
from onepledge_testtools.command_lines import UnwrappingStream

if TYPE_CHECKING:
    from conftest import Database

VARIABLES = ("PG_ADMIN_URL", "DATABASE_URL", "MIGRATION_DATABASE_URL", "DB_APP_ROLE", "PORT", "BIND_HOST")
HEAD = installed_head()
SETTING_REFUSAL = (
    "The role of DATABASE_URL is not least-privilege (ROLE_HAS_SETTING); remove the role's settings.\n"
)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


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


@pytest.fixture
def serve(migrated: Database, monkeypatch: pytest.MonkeyPatch) -> Callable[..., tuple[int, str, str]]:
    """`serve(**overrides)` runs `onepledge-outside serve` in process on the test's database."""

    def invoke(**overrides: str) -> tuple[int, str, str]:
        for name in VARIABLES:
            monkeypatch.delenv(name, raising=False)
        for name, value in migrated.environment(PORT=str(free_port()), **overrides).items():
            monkeypatch.setenv(name, value)
        out, err = io.StringIO(), UnwrappingStream()
        code = main(["serve"], Console(out, err))
        return code, out.getvalue(), err.getvalue()

    return invoke


@pytest.mark.unit
def test_the_checks_are_named_in_the_order_of_the_decision() -> None:
    assert CHECK_NAMES == (
        "settings",
        "database reachable",
        "database encoding",
        "session parameters",
        "schema in place",
        "role inspection",
        "bind probe",
    )


@pytest.mark.unit
@pytest.mark.parametrize("kind", [OperationalError, ProgrammingError])
def test_a_database_error_inside_a_check_is_that_checks_failure_with_its_sqlstate(
    kind: type[OperationalError] | type[ProgrammingError],
) -> None:
    def refused() -> None:
        raise kind("SELECT 1", {}, psycopg.errors.QueryCanceled("canceling statement due to timeout"))

    with pytest.raises(OperatorError) as raised:
        startup_check("role inspection", refused)
    assert raised.value.sentence == (
        "The database refused a statement of the startup check role inspection (SQLSTATE 57014); "
        "check the settings of the role of DATABASE_URL and of its database."
    )
    assert raised.value.code == "STARTUP_CHECK_FAILED"


@pytest.mark.unit
def test_a_check_that_passes_returns_its_value() -> None:
    assert startup_check("settings", lambda: 7) == 7


# WP1 review 4, failure 2: each of these ended `serve` in the last resort sentence.
@pytest.mark.integration
@pytest.mark.timeout(60)
def test_a_database_url_with_query_parameters_is_a_settings_sentence(
    serve: Callable[..., tuple[int, str, str]], migrated: Database
) -> None:
    url = migrated.app_url + "?options=-csearch_path%3Dpg_temp"
    assert serve(DATABASE_URL=url) == (
        1,
        "",
        "Configuration error: DATABASE_URL must not carry query parameters; remove the part from ? on.\n",
    )


@pytest.mark.integration
@pytest.mark.timeout(60)
@pytest.mark.parametrize(
    "setting", ["SET statement_timeout = '1ms'", "SET search_path = pg_temp, public"], ids=["timeout", "path"]
)
def test_a_role_setting_is_refused_by_the_role_inspection_not_the_last_resort(
    serve: Callable[..., tuple[int, str, str]],
    migrated: Database,
    admin: psycopg.Connection[Any],
    setting: str,
) -> None:
    admin.execute(sql.SQL("ALTER ROLE {} " + setting).format(sql.Identifier(migrated.app_role)))
    code, out, err = serve()
    assert (code, out, err) == (1, "", SETTING_REFUSAL)
    assert UNEXPECTED_ERROR not in err


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_a_statement_refused_inside_a_check_names_the_check_and_the_sqlstate(
    serve: Callable[..., tuple[int, str, str]], admin: psycopg.Connection[Any]
) -> None:
    admin.execute("REVOKE SELECT ON pg_catalog.pg_default_acl FROM PUBLIC")
    assert serve() == (
        1,
        "",
        "The database refused a statement of the startup check role inspection (SQLSTATE 42501); "
        "check the settings of the role of DATABASE_URL and of its database.\n",
    )


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_the_service_sets_its_session_parameters_whatever_the_database_defaults(
    migrated: Database, admin: psycopg.Connection[Any]
) -> None:
    database = sql.Identifier(migrated.target.database)
    admin.execute(sql.SQL("ALTER DATABASE {} SET statement_timeout = '1ms'").format(database))
    admin.execute(sql.SQL("ALTER DATABASE {} SET search_path = pg_temp").format(database))
    admin.execute(sql.SQL("ALTER DATABASE {} SET TimeZone = 'Asia/Kolkata'").format(database))
    engine = make_engine(migrated.app_url, service=True)
    try:
        with engine.connect() as connection:
            shown = {name: connection.exec_driver_sql(f"SHOW {name}").scalar() for name in SESSION_PARAMETERS}
    finally:
        engine.dispose()
    assert shown == dict(SESSION_PARAMETERS)
    assert shown["TimeZone"] == "UTC", "SPEC_DECISIONS 137: whatever the server or database default"


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_a_pooled_connection_whose_session_would_not_fire_triggers_is_refused(
    migrated: Database, admin: psycopg.Connection[Any]
) -> None:
    engine = make_engine(migrated.app_url, service=True)
    refuse_replica_sessions(engine)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar() == 1
        engine.dispose()
        admin.execute(
            sql.SQL("ALTER ROLE {} IN DATABASE {} SET session_replication_role = replica").format(
                sql.Identifier(migrated.app_role), sql.Identifier(migrated.target.database)
            )
        )
        with pytest.raises(OperationalError), engine.connect():
            pass
    finally:
        engine.dispose()


# SPEC_DECISIONS 128 and 132: the schema check reads the revision and passes only at head.
@pytest.mark.integration
@pytest.mark.timeout(60)
def test_a_database_never_migrated_says_its_tables_are_missing(
    provisioned_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in provisioned_database.environment(PORT=str(free_port())).items():
        monkeypatch.setenv(name, value)
    out, err = io.StringIO(), UnwrappingStream()
    assert main(["serve"], Console(out, err)) == 1
    assert err.getvalue() == "The outside records tables are missing; run db migrate first.\n"


@pytest.mark.integration
@pytest.mark.timeout(60)
@pytest.mark.parametrize("recorded", ["0001_schema", "9999_from_a_newer_program"])
def test_a_database_at_another_revision_names_both_revisions(
    serve: Callable[..., tuple[int, str, str]],
    migrated: Database,
    admin: psycopg.Connection[Any],
    recorded: str,
) -> None:
    admin.execute("UPDATE alembic_version SET version_num = %s", (recorded,))
    assert serve() == (
        1,
        "",
        f"The database {migrated.target.database} is at revision {recorded}, not at {HEAD} as this "
        "program expects; run db migrate with this program, or run the program that matches the "
        "database.\n",
    )


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_a_database_before_the_revision_grant_is_a_missing_privilege_of_the_role(
    serve: Callable[..., tuple[int, str, str]], migrated: Database
) -> None:
    MigrationRunner(migrated.owner_url, migrated.app_role).downgrade("0003_contract_checks")
    assert serve() == (
        1,
        "",
        "The role of DATABASE_URL is not least-privilege (TABLE_PRIVILEGE_MISSING); the database was "
        "not provisioned or migrated correctly; run db migrate as the owner.\n",
    )


# SPEC_DECISIONS 133.1: serve refuses a database whose encoding is not UTF8.
@pytest.mark.integration
@pytest.mark.timeout(60)
def test_a_database_that_is_not_utf8_is_refused_by_the_encoding_check(
    provisioned_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = provisioned_database.target
    with psycopg.connect(provisioned_database.admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(target.database)))
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'SQL_ASCII' LOCALE 'C' TEMPLATE template0").format(
                sql.Identifier(target.database), sql.Identifier(target.owner_role)
            )
        )
    # Provisioning again grants CONNECT; no migration is needed, the encoding is checked first.
    DatabaseProvisioner(provisioned_database.admin_url).provision(target)
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in provisioned_database.environment(PORT=str(free_port())).items():
        monkeypatch.setenv(name, value)
    out, err = io.StringIO(), UnwrappingStream()
    assert main(["serve"], Console(out, err)) == 1
    assert err.getvalue() == (
        "The database of DATABASE_URL has the encoding SQL_ASCII, not UTF8; create it again with "
        "db provision, which makes it UTF8.\n"
    )
