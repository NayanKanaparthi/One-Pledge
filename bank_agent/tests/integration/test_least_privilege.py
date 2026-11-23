"""One definition of a least-privilege app role (SPEC_DECISIONS 72, 103; review 2 F4, review 3 quality 1, 6).

Each of the ten points is broken by hand, as the superuser, on a fresh migrated database:
the startup guard refuses (one line naming DATABASE_URL, the first violation code and the
advice for it) and the integrity report's controls show it, `TRIGGERS_FIRE_FOR_APP_ROLE` false
for a setting or parameter (point 7) and `APP_ROLE_GRANTS_EXACT` false for every other point,
with the violation listed. A column privilege is never counted as held at table level.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from bank_testkit.database import BankDatabase
from onepledge_bank_agent.domain.integrity import DatabaseControl
from onepledge_bank_agent.infrastructure.db import role_check
from onepledge_bank_agent.infrastructure.db.connection_settings import session_parameter_problems
from onepledge_bank_agent.infrastructure.db.controls import SqlDatabaseControlInspector
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.migrations import head_revision
from onepledge_bank_agent.infrastructure.db.role_check import ADVICE, Violation
from onepledge_bank_agent.infrastructure.db.role_guard import (
    APP_ROLE_SENTENCE,
    OwnerRoleGuard,
    RuntimeRoleGuard,
)
from onepledge_bank_agent.infrastructure.operator_errors import OperatorError

pytestmark = pytest.mark.integration

ROLE_CONTROLS = (
    DatabaseControl.APP_ROLE_CANNOT_UPDATE_LOANS_RAW, DatabaseControl.APP_ROLE_CANNOT_DELETE_LOANS_RAW,
    DatabaseControl.APP_ROLE_CANNOT_UPDATE_AUDIT_LOG, DatabaseControl.APP_ROLE_CANNOT_DELETE_AUDIT_LOG,
    DatabaseControl.APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER, DatabaseControl.APP_ROLE_GRANTS_EXACT,
    DatabaseControl.TRIGGERS_FIRE_FOR_APP_ROLE,
)  # fmt: skip
EXACT, TRIGGERS = DatabaseControl.APP_ROLE_GRANTS_EXACT, DatabaseControl.TRIGGERS_FIRE_FOR_APP_ROLE
NOT_OWNER = DatabaseControl.APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER
RAW_AND_AUDIT = {
    DatabaseControl.APP_ROLE_CANNOT_UPDATE_LOANS_RAW, DatabaseControl.APP_ROLE_CANNOT_DELETE_LOANS_RAW,
    DatabaseControl.APP_ROLE_CANNOT_UPDATE_AUDIT_LOG, DatabaseControl.APP_ROLE_CANNOT_DELETE_AUDIT_LOG,
}  # fmt: skip


@dataclass(frozen=True)
class Broken:
    """A statement the superuser runs, the first violation it causes, the object named, and the
    role controls that turn false besides the one of its point (`control`)."""

    statement: str
    code: Violation
    named: str
    also_false: frozenset[DatabaseControl] = frozenset()
    control: DatabaseControl = EXACT


EXTRA = Violation.TABLE_PRIVILEGE_EXTRA
COLUMN, SETTING = Violation.COLUMN_PRIVILEGE, Violation.ROLE_HAS_SETTING
VIOLATIONS = [
    Broken("ALTER ROLE {app} CREATEDB", Violation.ROLE_HAS_ATTRIBUTE, "CREATEDB", frozenset({NOT_OWNER})),
    Broken("ALTER ROLE {app} CREATEROLE", Violation.ROLE_HAS_ATTRIBUTE, "CREATEROLE", frozenset({NOT_OWNER})),
    Broken("GRANT pg_write_all_data TO {app}", Violation.ROLE_HAS_MEMBERSHIP, "pg_write_all_data",
           frozenset({NOT_OWNER, *RAW_AND_AUDIT})),
    Broken("CREATE FUNCTION public.app_owned() RETURNS int LANGUAGE sql AS 'SELECT 1'; "
           "ALTER FUNCTION public.app_owned() OWNER TO {app}", Violation.ROLE_OWNS_OBJECT,
           "function public.app_owned", frozenset({NOT_OWNER})),
    Broken("GRANT TEMPORARY ON DATABASE {db} TO {app}", Violation.ROLE_HAS_DATABASE_PRIVILEGE, "TEMPORARY"),
    Broken("GRANT CREATE ON SCHEMA public TO {app}", Violation.ROLE_HAS_SCHEMA_PRIVILEGE, "public CREATE"),
    Broken("GRANT UPDATE (detail) ON audit_log TO {app}", COLUMN, "public.audit_log.detail UPDATE",
           frozenset({DatabaseControl.APP_ROLE_CANNOT_UPDATE_AUDIT_LOG})),
    Broken("GRANT UPDATE (raw_payload) ON loans_raw TO {app}", COLUMN, "public.loans_raw.raw_payload UPDATE",
           frozenset({DatabaseControl.APP_ROLE_CANNOT_UPDATE_LOANS_RAW})),
    Broken("GRANT SELECT (email) ON users TO {app}", COLUMN, "public.users.email SELECT"),
    Broken("GRANT TRUNCATE ON audit_log TO {app}", EXTRA, "public.audit_log TRUNCATE"),
    Broken("GRANT DELETE ON certificates TO {app}", EXTRA, "public.certificates DELETE"),
    Broken("GRANT UPDATE ON lenders TO {app}", EXTRA, "public.lenders UPDATE"),
    Broken("GRANT INSERT ON facilities TO PUBLIC", EXTRA, "public.facilities INSERT"),
    Broken("GRANT UPDATE ON alembic_version TO {app}", EXTRA, "public.alembic_version UPDATE"),
    Broken("REVOKE INSERT ON alerts FROM {app}", Violation.TABLE_PRIVILEGE_MISSING, "public.alerts INSERT"),
    Broken("CREATE TABLE public.stray (i int); ALTER TABLE public.stray OWNER TO {owner}",
           Violation.TABLE_NOT_IN_MATRIX, "public.stray"),
    Broken("GRANT USAGE ON SEQUENCE login_attempts_attempt_id_seq TO {app}", Violation.SEQUENCE_PRIVILEGE,
           "public.login_attempts_attempt_id_seq"),
    Broken("ALTER ROLE {app} IN DATABASE {db} SET session_replication_role = replica", SETTING,
           "in database", control=TRIGGERS),
    Broken("ALTER ROLE {app} SET statement_timeout = '1ms'", SETTING, "in every database", control=TRIGGERS),
    Broken("ALTER ROLE {app} SET search_path = pg_temp, public", SETTING, "in every database",
           control=TRIGGERS),
    Broken("GRANT SET ON PARAMETER session_replication_role TO {app}", Violation.PARAMETER_PRIVILEGE,
           "session_replication_role", control=TRIGGERS),
    Broken("ALTER DATABASE {db} SET session_replication_role = replica",
           Violation.TRIGGERS_DISABLED_FOR_SESSION, "database", control=TRIGGERS),
    Broken("CREATE FUNCTION public.optest_definer() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'; "
           "GRANT EXECUTE ON FUNCTION public.optest_definer() TO PUBLIC", Violation.SECURITY_DEFINER_FUNCTION,
           "public.optest_definer"),
    Broken("GRANT EXECUTE ON FUNCTION onepledge_loans_guard() TO PUBLIC", Violation.FUNCTION_PRIVILEGE,
           "public.onepledge_loans_guard"),
    Broken("ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public GRANT INSERT ON TABLES TO {app}",
           Violation.DEFAULT_PRIVILEGE, "tables"),
    Broken("GRANT SELECT ON lenders TO {app} WITH GRANT OPTION", Violation.GRANT_OPTION,
           "table public.lenders SELECT"),
]  # fmt: skip


@pytest.fixture
def app_engine(bank_db: BankDatabase) -> Iterator[Engine]:
    engine = make_engine(bank_db.app_url, inspecting=True)
    yield engine
    engine.dispose()


def run_as_superuser(database: BankDatabase, statements: str) -> None:
    names = {
        "app": sql.Identifier(database.app_role),
        "owner": sql.Identifier(database.target.owner_role),
        "db": sql.Identifier(database.name),
    }
    with database.connect("admin") as connection:
        for statement in statements.split("; "):
            connection.execute(sql.SQL(statement).format(**names))


def test_a_freshly_migrated_app_role_is_least_privilege(bank_db: BankDatabase, app_engine: Engine) -> None:
    assert RuntimeRoleGuard(app_engine).violations() == ()
    inspector = SqlDatabaseControlInspector(app_engine, head_revision())
    assert all(inspector.controls().values()) and inspector.violations() == ()


@pytest.mark.parametrize("broken", VIOLATIONS, ids=[v.statement for v in VIOLATIONS])
def test_each_violation_is_refused_by_the_guard_and_shown_by_the_report(
    bank_db: BankDatabase, app_engine: Engine, broken: Broken
) -> None:
    code, named = broken.code, broken.named
    run_as_superuser(bank_db, broken.statement)
    guard = RuntimeRoleGuard(app_engine)
    found = guard.violations()
    assert [(v.code, v.object_name) for v in found][:1] == [(code, found[0].object_name)]
    assert any(v.code is code and named in v.object_name for v in found), found
    with pytest.raises(OperatorError) as refused:
        guard.require_least_privilege()
    assert refused.value.code == "ROLE_NOT_LEAST_PRIVILEGE"
    assert refused.value.sentence == (
        f"The role of DATABASE_URL is not least-privilege ({found[0].code}); {ADVICE[found[0].code]}."
    )
    inspector = SqlDatabaseControlInspector(app_engine, head_revision())
    controls = inspector.controls()
    false = {control for control in ROLE_CONTROLS if not controls[control]}
    assert false == {broken.control, *broken.also_false}
    assert any(
        v.code == code.value and v.object is not None and named in v.object for v in inspector.violations()
    )


def test_a_superuser_and_the_owner_are_refused_and_shown(bank_db: BankDatabase) -> None:
    superuser = make_engine(bank_db.admin_url.rsplit("/", 1)[0] + "/" + bank_db.name)
    owner, app = make_engine(bank_db.owner_url), make_engine(bank_db.app_url)
    try:
        assert RuntimeRoleGuard(superuser).violations()[0].code is Violation.ROLE_IS_SUPERUSER
        assert RuntimeRoleGuard(owner).violations()[0].code is Violation.ROLE_OWNS_OBJECT
        controls = SqlDatabaseControlInspector(owner, head_revision()).controls()
        assert not controls[DatabaseControl.APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER]
        assert not controls[DatabaseControl.APP_ROLE_GRANTS_EXACT]
        OwnerRoleGuard(owner).require_owner()
        with pytest.raises(OperatorError) as refused:
            OwnerRoleGuard(app).require_owner()
        assert refused.value.code == "ROLE_NOT_OWNER"
    finally:
        for engine in (superuser, owner, app):
            engine.dispose()


def test_the_app_role_inserts_into_the_identity_table_without_any_sequence_privilege(
    bank_db: BankDatabase,
) -> None:
    with bank_db.connect("app") as connection:
        connection.execute(
            "INSERT INTO login_attempts (email_sha256, client_addr, succeeded, at) "
            "VALUES (repeat('a', 64), '127.0.0.1', false, now())"
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT nextval('login_attempts_attempt_id_seq')")


def test_a_runtime_connection_whose_session_does_not_fire_triggers_is_refused(bank_db: BankDatabase) -> None:
    """SPEC_DECISIONS 103.7: the check of `session_replication_role` runs on every new pooled connection."""
    run_as_superuser(bank_db, "ALTER ROLE {app} IN DATABASE {db} SET session_replication_role = replica")
    engine = make_engine(bank_db.app_url)
    try:
        with pytest.raises(OperationalError), engine.connect():
            pass
    finally:
        engine.dispose()
    inspecting = make_engine(bank_db.app_url, inspecting=True)
    try:
        found = RuntimeRoleGuard(inspecting).violations()
    finally:
        inspecting.dispose()
    assert (Violation.TRIGGERS_DISABLED_FOR_SESSION, "session") in {(v.code, v.object_name) for v in found}


def test_every_connection_carries_the_bank_agents_own_session_parameters(bank_db: BankDatabase) -> None:
    run_as_superuser(bank_db, "ALTER ROLE {app} SET statement_timeout = '1ms'")
    engine = make_engine(bank_db.app_url, inspecting=True)
    try:
        with engine.connect() as connection:
            assert session_parameter_problems(connection) == []
    finally:
        engine.dispose()


def test_the_owner_check_says_whether_the_role_or_a_table_is_wrong(bank_db: BankDatabase) -> None:
    run_as_superuser(bank_db, "CREATE TABLE public.optest_foreign (i int); "
                              "ALTER TABLE public.optest_foreign OWNER TO {app}")  # fmt: skip
    owner, app = make_engine(bank_db.owner_url), make_engine(bank_db.app_url, inspecting=True)
    try:
        with pytest.raises(OperatorError) as foreign:
            OwnerRoleGuard(owner).require_owner()
        with pytest.raises(OperatorError) as not_owner:
            OwnerRoleGuard(app).require_owner()
        OwnerRoleGuard(owner).require_app_role(bank_db.app_role)
        for wrong in (bank_db.target.owner_role, "optest_other_app"):
            with pytest.raises(OperatorError) as refused:
                OwnerRoleGuard(owner).require_app_role(wrong)
            assert refused.value.sentence == APP_ROLE_SENTENCE
    finally:
        owner.dispose()
        app.dispose()
    assert (
        foreign.value.sentence == role_check.FOREIGN_TABLE
        and not_owner.value.sentence == role_check.NOT_OWNER
    )
