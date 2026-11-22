"""`db migrate` failures (SPEC_DECISIONS 79; review 2, quality finding 7): each is a migration failure.

Each case provokes a real failure on PostgreSQL and expects the fixed sentence naming the
revision (or, for a connection that never opened, the sentence naming that cause), one
`schema_migration_failed` record with `exception_type` and `revision`, exit 1, and never
the last resort sentence.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from bank_testkit.database import BankDatabase, drop, new_bank_database
from bank_testkit.programs import run_program, split_stderr
from onepledge_bank_agent.infrastructure.db.provisioning import DatabaseProvisioner

pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]

LAST_RESORT = "Unexpected error. Run with --debug for details."
FIRST = "0001_schema"


@pytest.fixture
def provisioned() -> Iterator[BankDatabase]:
    """A provisioned bank database that is not migrated yet; dropped afterwards."""
    database = new_bank_database()
    DatabaseProvisioner(database.admin_url).provision(database.target)
    yield database
    drop(database)


def failed_sentence(revision: str) -> str:
    return (
        f"The migration to {revision} failed and was rolled back. Fix the database state named in the log "
        "and run db migrate again."
    )


def migrate(environment: dict[str, str]) -> tuple[int, dict[str, Any], list[dict[str, Any]], list[str]]:
    """`db migrate --json` in a child process: exit code, the document, the log records, the sentences."""
    finished = run_program(
        "onepledge-bank", ["db", "migrate", "--json"], {**environment, "LOG_LEVEL": "INFO"}
    )
    records, sentences = split_stderr(finished.stderr)
    return finished.returncode, json.loads(finished.stdout), records, sentences


def failures(records: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return [(r["event"], r["revision"], r["exception_type"]) for r in records if r["level"] == "error"]


def test_a_conflicting_table_is_a_failed_migration_with_its_revision(provisioned: BankDatabase) -> None:
    with provisioned.connect("owner") as owner:
        owner.execute("CREATE TABLE lenders (x integer)")
    code, document, records, sentences = migrate(provisioned.environment())
    assert (code, sentences) == (1, [failed_sentence(FIRST)])
    assert failures(records) == [("schema_migration_failed", FIRST, "ProgrammingError")]
    assert document == {"ok": False, "error": {"code": "OPERATION_FAILED", "message": failed_sentence(FIRST)}}
    with provisioned.connect("admin") as admin:
        tables = admin.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'lenders'"
        ).fetchone()
    assert tables == (0,), "the failed migration was rolled back"


def test_a_missing_privilege_is_a_failed_migration_with_its_revision(provisioned: BankDatabase) -> None:
    with provisioned.connect("admin") as admin:
        admin.execute("REVOKE CREATE ON SCHEMA public FROM pg_database_owner")
    code, document, records, sentences = migrate(provisioned.environment())
    assert (code, sentences) == (1, [failed_sentence(FIRST)])
    assert failures(records) == [("schema_migration_failed", FIRST, "ProgrammingError")]
    assert LAST_RESORT not in json.dumps(document)


def test_an_unreachable_database_names_that_cause_and_is_still_recorded(provisioned: BankDatabase) -> None:
    unreachable = "postgresql://nobody:hunter2-secret@127.0.0.1:1/none"
    code, document, records, sentences = migrate(
        {**provisioned.environment(), "MIGRATION_DATABASE_URL": unreachable}
    )
    sentence = "PostgreSQL is not reachable at MIGRATION_DATABASE_URL; start it or fix the URL."
    assert (code, sentences, document["error"]["message"]) == (1, [sentence], sentence)
    assert failures(records) == [("schema_migration_failed", "head", "OperationalError")]
    assert "hunter2" not in json.dumps(document) + json.dumps(records)
