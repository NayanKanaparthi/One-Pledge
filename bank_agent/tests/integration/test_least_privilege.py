"""One definition of a least-privilege app role (SPEC_DECISIONS 72; review 2, F4 and quality finding 4).

Each of the six points is broken by hand, as the superuser, on a fresh migrated database:
the startup guard refuses (one line naming DATABASE_URL and the first violation code) and
the integrity report's controls show it, `APP_ROLE_GRANTS_EXACT` false with the violation
listed. Before this decision a column level UPDATE on `audit_log` passed both.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Engine

from bank_testkit.database import BankDatabase
from onepledge_bank_agent.domain.integrity import DatabaseControl
from onepledge_bank_agent.infrastructure.db.controls import SqlDatabaseControlInspector
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.migrations import head_revision
from onepledge_bank_agent.infrastructure.db.role_check import Violation
from onepledge_bank_agent.infrastructure.db.role_guard import OwnerRoleGuard, RuntimeRoleGuard
from onepledge_bank_agent.infrastructure.operator_errors import OperatorError

pytestmark = pytest.mark.integration

ROLE_CONTROLS = (
    DatabaseControl.APP_ROLE_CANNOT_UPDATE_LOANS_RAW, DatabaseControl.APP_ROLE_CANNOT_DELETE_LOANS_RAW,
    DatabaseControl.APP_ROLE_CANNOT_UPDATE_AUDIT_LOG, DatabaseControl.APP_ROLE_CANNOT_DELETE_AUDIT_LOG,
    DatabaseControl.APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER, DatabaseControl.APP_ROLE_GRANTS_EXACT,
)  # fmt: skip
NOT_OWNER = DatabaseControl.APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER
RAW_AND_AUDIT = {
    DatabaseControl.APP_ROLE_CANNOT_UPDATE_LOANS_RAW, DatabaseControl.APP_ROLE_CANNOT_DELETE_LOANS_RAW,
    DatabaseControl.APP_ROLE_CANNOT_UPDATE_AUDIT_LOG, DatabaseControl.APP_ROLE_CANNOT_DELETE_AUDIT_LOG,
}  # fmt: skip


@dataclass(frozen=True)
class Broken:
    """A statement the superuser runs, the first violation it causes, the object named, and the
    role controls that turn false besides APP_ROLE_GRANTS_EXACT."""

    statement: str
    code: Violation
    named: str
    also_false: frozenset[DatabaseControl] = frozenset()


EXTRA = Violation.TABLE_PRIVILEGE_EXTRA
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
    Broken("GRANT UPDATE (detail) ON audit_log TO {app}", EXTRA, "public.audit_log UPDATE",
           frozenset({DatabaseControl.APP_ROLE_CANNOT_UPDATE_AUDIT_LOG})),
    Broken("GRANT UPDATE (raw_payload) ON loans_raw TO {app}", EXTRA, "public.loans_raw UPDATE",
           frozenset({DatabaseControl.APP_ROLE_CANNOT_UPDATE_LOANS_RAW})),
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
]  # fmt: skip


@pytest.fixture
def app_engine(bank_db: BankDatabase) -> Iterator[Engine]:
    engine = make_engine(bank_db.app_url)
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
        f"The role of DATABASE_URL is not least-privilege ({found[0].code}); use the app role."
    )
    inspector = SqlDatabaseControlInspector(app_engine, head_revision())
    controls = inspector.controls()
    false = {control for control in ROLE_CONTROLS if not controls[control]}
    assert false == {DatabaseControl.APP_ROLE_GRANTS_EXACT, *broken.also_false}
    assert any(text.startswith(code.value) and named in text for text in inspector.violations())


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
