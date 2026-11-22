"""The migrated schema (SPEC 12.1, 12.5): all 18 tables, models equal migrations, CHECK lists equal enums."""

from __future__ import annotations

import re
from enum import StrEnum

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from bank_testkit.database import BankDatabase, pairs
from onepledge_bank_agent.domain import enums
from onepledge_bank_agent.domain.cleaning.rows import FIELD_ORDER
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.migrations import MigrationRunner, SchemaInspector, head_revision
from onepledge_bank_agent.infrastructure.db.models import Base
from onepledge_bank_agent.infrastructure.operator_errors import OperatorError

pytestmark = pytest.mark.integration

TABLES = {
    "lenders", "facilities", "users", "login_attempts", "tape_submissions", "loans_raw", "cleaning_issues",
    "submission_items", "loans", "local_checks", "loan_tokens", "registry_requests", "registry_request_items",
    "certificates", "alerts", "checkpoints_seen", "audit_log", "integrity_runs",
}  # fmt: skip
ENUM_CHECKS: dict[str, type[StrEnum] | tuple[str, ...]] = {
    "ck_facilities_status": enums.FacilityStatus,
    "ck_users_role": enums.Role,
    "ck_tape_submissions_kind": enums.TapeKind,
    "ck_tape_submissions_source": enums.TapeSource,
    "ck_tape_submissions_status": enums.TapeStatus,
    "ck_tape_submissions_failure_code": enums.FailureCode,
    "ck_cleaning_issues_problem": enums.CleaningProblem,
    "ck_cleaning_issues_action": enums.CleaningAction,
    "ck_cleaning_issues_field": FIELD_ORDER,
    "ck_submission_items_disposition": enums.RowDisposition,
    "ck_loans_state": enums.LoanState,
    "ck_local_checks_check_type": enums.CheckType,
    "ck_local_checks_result": enums.CheckOutcome,
    "ck_local_checks_reason_code": enums.CheckReason,
    "ck_loan_tokens_token_kind": enums.TokenKind,
    "ck_registry_requests_origin": enums.RequestOrigin,
    "ck_registry_requests_kind": enums.TapeKind,
    "ck_registry_request_items_result": enums.RegistryItemResult,
    "ck_registry_request_items_match_kind": enums.MatchKind,
    "ck_registry_request_items_match_basis": enums.MatchBasis,
    "ck_certificates_kind": enums.TapeKind,
    "ck_certificates_failure_reason": enums.CertificateFailure,
    "ck_checkpoints_seen_failure_reason": enums.CheckpointFailure,
    "ck_alerts_role": enums.AlertRole,
    "ck_alerts_match_kind": enums.MatchKind,
    "ck_alerts_match_basis": enums.MatchBasis,
    "ck_alerts_explanation_source": enums.ExplanationSource,
    "ck_alerts_status": enums.AlertStatus,
    "ck_alerts_case_status": enums.CaseStatus,
    "ck_alerts_bank_position": enums.BankPosition,
    "ck_audit_log_actor_type": enums.ActorType,
    "ck_audit_log_action": enums.AuditAction,
    "ck_audit_log_object_type": enums.AuditObjectType,
    "ck_audit_log_outcome": enums.AuditOutcome,
    "ck_integrity_runs_status": enums.IntegrityRunStatus,
    "ck_registry_request_items_kinds_matched": enums.TokenKind,
    "ck_alerts_kinds_matched": enums.TokenKind,
}

SNAPSHOT = {
    "columns": "SELECT table_name, column_name, data_type, is_nullable, coalesce(column_default, '') "
    "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2",
    "constraints": "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2",
    "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' "
    "ORDER BY 1, 2",
    "triggers": "SELECT tgrelid::regclass::text, tgname, pg_get_triggerdef(oid) FROM pg_trigger "
    "WHERE NOT tgisinternal ORDER BY 1, 2",
    "functions": "SELECT proname, md5(prosrc) FROM pg_proc WHERE pronamespace = 'public'::regnamespace "
    "ORDER BY 1",
    "grants": "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
    "WHERE grantee = current_setting('onepledge.app_role') ORDER BY 1, 2",
}


def snapshot(database: BankDatabase) -> dict[str, list[tuple[object, ...]]]:
    with database.connect("admin") as connection:
        connection.execute(f"SET onepledge.app_role = '{database.app_role}'")
        return {name: connection.execute(query).fetchall() for name, query in SNAPSHOT.items()}


def test_every_table_of_spec_12_1_exists_owned_by_the_owner_role(bank_db: BankDatabase) -> None:
    owners = pairs(bank_db, "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public'")
    assert set(owners) == TABLES | {"alembic_version"}
    assert set(owners.values()) == {bank_db.target.owner_role}


def test_upgrade_downgrade_upgrade_leaves_an_identical_schema(bank_db: BankDatabase) -> None:
    before = snapshot(bank_db)
    assert before["triggers"] and before["grants"]
    runner = MigrationRunner(bank_db.owner_url, bank_db.app_role)
    runner.downgrade("base")
    emptied = snapshot(bank_db)
    assert (
        [row[0] for row in emptied["columns"]] == ["alembic_version"]
        and not emptied["triggers"]
        and not emptied["functions"]
    )
    runner.upgrade()
    assert snapshot(bank_db) == before


def test_the_models_equal_the_migrated_schema(bank_db: BankDatabase) -> None:
    engine = make_engine(bank_db.owner_url)
    try:
        with engine.connect() as connection:
            differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        engine.dispose()
    assert differences == []


def _values(definition: str) -> set[str]:
    return set(re.findall(r"'([A-Za-z0-9_]+)'::text", definition))


def test_every_status_and_code_check_lists_exactly_the_enum_values(bank_db: BankDatabase) -> None:
    checks = pairs(
        bank_db,
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE contype = 'c' "
        "AND connamespace = 'public'::regnamespace",
    )
    for name, values in ENUM_CHECKS.items():
        expected = {str(v) for v in values}
        assert _values(str(checks[name])) == expected, name
    assert "ck_local_checks_combination" in checks and "ck_loans_current_balance_cents" in checks


def test_the_schema_status_reads_the_revision_as_the_app_role(bank_db: BankDatabase) -> None:
    engine = make_engine(bank_db.app_url)
    try:
        status = SchemaInspector(engine).status()
        assert (status.current_revision, status.head_revision, status.at_head) == (
            head_revision(),
            "0004_checkpoint_body",
            True,
        )
        SchemaInspector(engine).require_head()
        MigrationRunner(bank_db.owner_url, bank_db.app_role).downgrade("0002_guards")
        with pytest.raises(OperatorError) as raised:
            SchemaInspector(engine).require_head()
        assert raised.value.sentence == "The database at DATABASE_URL cannot be read; run db migrate first."
    finally:
        engine.dispose()
    MigrationRunner(bank_db.owner_url, bank_db.app_role).upgrade()


def test_the_grants_migration_refuses_a_missing_app_role(bank_db: BankDatabase) -> None:
    runner = MigrationRunner(bank_db.owner_url, bank_db.app_role)
    runner.downgrade("0002_guards")
    with pytest.raises(OperatorError) as raised:
        MigrationRunner(bank_db.owner_url, "no_such_role_here").upgrade()
    assert "DB_APP_ROLE" in raised.value.sentence
    runner.upgrade()
    with psycopg.connect(bank_db.app_url) as connection:
        assert connection.execute("SELECT count(*) FROM loans").fetchone() == (0,)
