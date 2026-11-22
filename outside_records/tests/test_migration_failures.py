"""`db migrate` failures (SPEC_DECISIONS 79): every exception while migrating is a migration failure.

Each case provokes a real failure on PostgreSQL and shows the fixed sentence (or, for a
connection that never opened, the sentence naming that cause), one `schema_migration_failed`
record with `exception_type` and `revision`, exit 1, and never the last resort sentence.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from onepledge_outside_records.interface.cli import main
from onepledge_outside_records.interface.output import Console

if TYPE_CHECKING:
    from conftest import Database

pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]

VARIABLES = ("PG_ADMIN_URL", "DATABASE_URL", "MIGRATION_DATABASE_URL", "DB_APP_ROLE", "LOG_LEVEL")
LAST_RESORT = "Unexpected error. Run with --debug for details."


def failed_sentence(revision: str) -> str:
    return (
        f"The migration to {revision} failed and was rolled back. "
        "Fix the database state named in the log and run db migrate again."
    )


@pytest.fixture
def migrate(monkeypatch: pytest.MonkeyPatch) -> Callable[..., tuple[int, str, list[dict[str, Any]], str]]:
    """Runs `db migrate --json` in process: code, stdout, the log records, the last stderr line."""

    def invoke(**environment: str) -> tuple[int, str, list[dict[str, Any]], str]:
        for name in VARIABLES:
            monkeypatch.delenv(name, raising=False)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        out, err = io.StringIO(), io.StringIO()
        code = main(["db", "migrate", "--json"], Console(out, err))
        lines = err.getvalue().splitlines()
        records = [json.loads(line) for line in lines if line.startswith("{")]
        # The sentence may wrap at 100 columns onto indented continuation lines (SPEC_DECISIONS 78).
        sentences = [line.strip() for line in lines if not line.startswith("{")]
        return code, out.getvalue(), records, " ".join(sentences)

    return invoke


def failures(records: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return [(r["event"], r["revision"], r["exception_type"]) for r in records if r["level"] == "error"]


def test_a_conflicting_table_is_a_failed_migration_with_its_revision(
    provisioned_database: Database, migrate: Callable[..., tuple[int, str, list[dict[str, Any]], str]]
) -> None:
    with psycopg.connect(provisioned_database.owner_url, autocommit=True) as owner:
        owner.execute("CREATE TABLE payments (x integer)")
    code, out, records, sentence = migrate(**provisioned_database.environment(LOG_LEVEL="INFO"))
    assert code == 1
    assert sentence == failed_sentence("0001_schema")
    assert failures(records) == [("schema_migration_failed", "0001_schema", "ProgrammingError")]
    assert json.loads(out) == {
        "ok": False,
        "error": {"code": "OPERATION_FAILED", "message": failed_sentence("0001_schema")},
    }


def test_a_missing_privilege_is_a_failed_migration_with_its_revision(
    provisioned_database: Database, migrate: Callable[..., tuple[int, str, list[dict[str, Any]], str]]
) -> None:
    database = provisioned_database.target.database
    with psycopg.connect(provisioned_database.admin_url, autocommit=True, dbname=database) as admin:
        admin.execute("REVOKE CREATE ON SCHEMA public FROM pg_database_owner")
    code, out, records, sentence = migrate(**provisioned_database.environment(LOG_LEVEL="INFO"))
    assert (code, sentence) == (1, failed_sentence("0001_schema"))
    assert failures(records) == [("schema_migration_failed", "0001_schema", "ProgrammingError")]
    assert LAST_RESORT not in out


def test_an_unreachable_database_names_that_cause_and_is_still_recorded(
    provisioned_database: Database, migrate: Callable[..., tuple[int, str, list[dict[str, Any]], str]]
) -> None:
    unreachable = "postgresql://nobody:secret@127.0.0.1:1/none"
    env = provisioned_database.environment(MIGRATION_DATABASE_URL=unreachable, LOG_LEVEL="INFO")
    code, out, records, sentence = migrate(**env)
    assert code == 1
    assert sentence == "PostgreSQL is not reachable at MIGRATION_DATABASE_URL; start it or fix the URL."
    assert failures(records) == [("schema_migration_failed", "head", "OperationalError")]
    assert "secret" not in out + sentence + json.dumps(records)
