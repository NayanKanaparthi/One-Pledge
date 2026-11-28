"""The key holder database on real PostgreSQL 16: schema, roles, the journal, registration.

Every test gets its own database, made by the package's own `db provision` and `db migrate`
code. The serving code connects as the app role; the tests read what it wrote as the owner.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import oprf
import psycopg
import pytest
import structlog
from fastapi import FastAPI
from onepledge_key_holder.domain.errors import ServiceUnavailableError
from onepledge_key_holder.domain.evaluation import EvaluationRecord
from onepledge_key_holder.domain.identity import HolderId, KeyVersion
from onepledge_key_holder.infrastructure.db.engines import DatabaseTarget, OperatorError
from onepledge_key_holder.infrastructure.db.migrations import MigrationRunner
from onepledge_key_holder.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_key_holder.infrastructure.db.registration import ConfigurationRegistrar
from onepledge_key_holder.infrastructure.db.repositories import Database
from onepledge_key_holder.infrastructure.settings import ServeSettings
from onepledge_key_holder.interface.composition import build_app

from onepledge_testtools.network import free_port

if TYPE_CHECKING:
    from conftest import ScratchDatabase

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
DAY = datetime(2026, 11, 16, tzinfo=UTC)
COLUMNS = (
    "SELECT table_name, column_name FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name <> 'alembic_version' ORDER BY table_name, ordinal_position"
)


def record(member_id: str, items: int, at: datetime = NOW) -> EvaluationRecord:
    return EvaluationRecord(uuid.uuid4(), member_id, str(uuid.uuid4()), items, at)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_the_tables_and_columns_are_exactly_those_of_spec_4_1(database: ScratchDatabase) -> None:
    columns: dict[str, list[str]] = {}
    for table, column in database.rows(COLUMNS):
        columns.setdefault(table, []).append(column)
    assert columns == {
        "api_credentials": ["credential_id", "member_id", "key_sha256", "active"],
        "evaluation_log": ["eval_id", "member_id", "request_id", "item_count", "at"],
        "key_versions": ["key_version", "status", "created_at"],
        "rate_limits": ["member_id", "window_start", "items_used"],
    }


def test_migrate_records_the_active_key_version_and_only_the_hash_of_the_member_key(
    database: ScratchDatabase,
) -> None:
    assert [row[:2] for row in database.rows("SELECT key_version, status, created_at FROM key_versions")] == [
        (1, "ACTIVE")
    ]
    rows = database.rows("SELECT member_id::text, key_sha256, active FROM api_credentials")
    assert rows == [(database.member_id, sha256_hex(database.api_key), True)]
    assert database.api_key not in str(database.rows("SELECT * FROM api_credentials"))


def test_the_app_role_appends_to_the_log_and_cannot_read_update_or_delete_it(
    database: ScratchDatabase,
) -> None:
    serving = Database(database.app_url)
    try:
        assert serving.journal().accept(record(database.member_id, 3), DAY, 10) is True
    finally:
        serving.dispose()
    for statement in (
        "UPDATE evaluation_log SET item_count = 1",
        "DELETE FROM evaluation_log",
        "TRUNCATE evaluation_log",
        "SELECT * FROM evaluation_log",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            database.execute(statement, role="app")
    assert database.rows("SELECT item_count FROM evaluation_log") == [(3,)]


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO api_credentials VALUES (gen_random_uuid(), gen_random_uuid(), repeat('a', 64), true)",
        "UPDATE api_credentials SET active = true",
        "DELETE FROM api_credentials",
        "INSERT INTO key_versions VALUES (2, 'RETIRED_READABLE', now())",
        "UPDATE key_versions SET status = 'RETIRED_READABLE'",
        "DELETE FROM rate_limits",
        "CREATE TABLE elements (value text)",
    ],
)
def test_the_app_role_cannot_change_credentials_key_versions_or_the_schema(
    database: ScratchDatabase, statement: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        database.execute(statement, role="app")


def test_the_journal_counts_and_logs_together_or_writes_nothing(database: ScratchDatabase) -> None:
    serving = Database(database.app_url)
    try:
        journal = serving.journal()
        first = record(database.member_id, 6)
        assert journal.accept(first, DAY, 10) is True
        assert journal.accept(record(database.member_id, 6), DAY, 10) is False
        assert journal.accept(record(database.member_id, 11), DAY + timedelta(days=5), 10) is False
        assert journal.accept(record(database.member_id, 4), DAY, 10) is True
        assert journal.accept(record(database.member_id, 1), DAY, 10) is False
        # The next UTC day is a new window, and another member has its own counter.
        assert journal.accept(
            record(database.member_id, 10, NOW + timedelta(days=1)), DAY + timedelta(days=1), 10
        )
        assert journal.accept(record(str(uuid.uuid4()), 10), DAY, 10) is True
    finally:
        serving.dispose()
    mine = uuid.UUID(database.member_id)
    counters = database.rows("SELECT member_id, window_start, items_used FROM rate_limits ORDER BY 2")
    assert [row[1:] for row in counters if row[0] == mine] == [(DAY, 10), (DAY + timedelta(days=1), 10)]
    logged = database.rows("SELECT member_id, item_count FROM evaluation_log ORDER BY at, item_count")
    assert [row[1] for row in logged if row[0] == mine] == [4, 6, 10]
    stored = database.rows(
        "SELECT eval_id, member_id::text, request_id::text, item_count, at FROM evaluation_log"
    )
    assert (first.eval_id, first.member_id, first.request_id, 6, NOW) in stored


def test_concurrent_requests_never_pass_the_cap(database: ScratchDatabase) -> None:
    serving = Database(database.app_url)
    accepted: list[bool] = []
    barrier = threading.Barrier(12)

    def one() -> None:
        barrier.wait()
        accepted.append(serving.journal().accept(record(database.member_id, 3), DAY, 20))

    threads = [threading.Thread(target=one) for _ in range(12)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        serving.dispose()
    assert accepted.count(True) == 6
    assert database.rows("SELECT items_used FROM rate_limits") == [(18,)]
    assert database.rows("SELECT count(*), sum(item_count) FROM evaluation_log") == [(6, 18)]


def test_credentials_are_looked_up_by_hash_and_switched_off_when_no_longer_listed(
    database: ScratchDatabase,
) -> None:
    serving = Database(database.app_url)
    try:
        directory = serving.credentials()
        known = sha256_hex(database.api_key)
        assert directory.member_for(known) == database.member_id
        assert directory.member_for(sha256_hex("opk_unknown")) is None
        newcomer, new_hash = str(uuid.uuid4()), sha256_hex("opk_new")
        ConfigurationRegistrar(database.owner_url).register(KeyVersion(1), {newcomer: new_hash}, NOW)
        assert directory.member_for(known) is None
        assert directory.member_for(new_hash) == newcomer
        # Listing the first hash again switches the same row back on.
        both = {database.member_id: known, newcomer: new_hash}
        ConfigurationRegistrar(database.owner_url).register(KeyVersion(1), both, NOW)
        assert directory.member_for(known) == database.member_id
    finally:
        serving.dispose()
    assert database.rows("SELECT count(*) FROM api_credentials") == [(2,)]
    assert database.rows("SELECT count(*) FROM key_versions") == [(1,)]


def test_a_second_active_key_version_is_refused_because_rotation_is_not_built(
    database: ScratchDatabase,
) -> None:
    with pytest.raises(OperatorError, match="Key rotation is not part of this version"):
        ConfigurationRegistrar(database.owner_url).register(KeyVersion(2), {}, NOW)
    assert database.rows("SELECT key_version, status FROM key_versions") == [(1, "ACTIVE")]
    # The refusal changed nothing: the member credential is still active.
    assert database.rows("SELECT active FROM api_credentials") == [(True,)]
    with pytest.raises(psycopg.errors.UniqueViolation):
        database.execute("INSERT INTO key_versions VALUES (2, 'ACTIVE', now())")


def test_serve_starts_only_on_the_key_version_the_database_records(
    database: ScratchDatabase, fresh_database: ScratchDatabase
) -> None:
    serving = Database(database.app_url)
    try:
        serving.require_active(KeyVersion(1))
        with pytest.raises(OperatorError, match="ACTIVE_KEY_VERSION is not the active key version"):
            serving.require_active(KeyVersion(2))
    finally:
        serving.dispose()
    DatabaseProvisioner(fresh_database.admin_url).provision(fresh_database.target)
    unmigrated = Database(fresh_database.app_url)
    try:
        with pytest.raises(OperatorError, match="run db provision and db migrate first"):
            unmigrated.require_active(KeyVersion(1))
    finally:
        unmigrated.dispose()


def test_provisioning_twice_keeps_the_database_and_migrating_needs_the_app_role(
    fresh_database: ScratchDatabase,
) -> None:
    with pytest.raises(OperatorError, match="MIGRATION_DATABASE_URL"):
        MigrationRunner(fresh_database.owner_url).upgrade()
    provisioner = DatabaseProvisioner(fresh_database.admin_url)
    provisioner.provision(fresh_database.target)
    assert MigrationRunner(fresh_database.owner_url).upgrade() == "0002_grants"
    provisioner.provision(fresh_database.target)
    assert MigrationRunner(fresh_database.owner_url).upgrade() == "0002_grants"
    assert fresh_database.rows("SELECT version_num FROM alembic_version") == [("0002_grants",)]
    MigrationRunner(fresh_database.owner_url).downgrade()
    assert fresh_database.rows(COLUMNS) == []
    assert "password" not in repr(fresh_database.target).lower()
    assert isinstance(fresh_database.target, DatabaseTarget)


def test_a_database_that_does_not_answer_is_service_unavailable() -> None:
    serving = Database(f"postgresql://kh_app:secret@127.0.0.1:{free_port()}/kh")
    try:
        with pytest.raises(ServiceUnavailableError):
            serving.credentials().member_for("a" * 64)
        with pytest.raises(ServiceUnavailableError):
            serving.journal().accept(record(str(uuid.uuid4()), 1), DAY, 10)
        with pytest.raises(OperatorError):
            serving.require_active(KeyVersion(1))
    finally:
        serving.dispose()


def call(app: FastAPI, payload: dict[str, object], api_key: str) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/v1/evaluate", json=payload, headers={"X-API-Key": api_key})

    return asyncio.run(send())


def test_an_evaluation_through_the_real_app_leaves_one_row_and_no_element_anywhere(
    database: ScratchDatabase, point: Callable[[bytes], str]
) -> None:
    scalar = bytes(oprf.mask.random())
    settings = ServeSettings(
        HolderId.KH1, KeyVersion(1), "127.0.0.1", 8301, database.app_url, scalar, daily_element_cap=5
    )
    serving = Database(database.app_url)
    structlog.reset_defaults()
    try:
        app = build_app(settings, serving)
        sent = [point(b"vin"), point(b"borrower")]
        request_id = str(uuid.uuid4())
        payload: dict[str, object] = {"request_id": request_id, "key_version": None, "elements": sent}
        answer = call(app, payload, database.api_key)
        assert answer.status_code == 200
        expected = [oprf.mask(scalar).mask(oprf.data.from_base64(element)).to_base64() for element in sent]
        assert answer.json() == {
            "request_id": request_id,
            "holder_id": "kh1",
            "key_version": 1,
            "elements": expected,
        }
        rejected = call(
            app, {**payload, "elements": [sent[0], base64.b64encode(bytes(32)).decode()]}, database.api_key
        )
        limited = call(app, {**payload, "elements": sent * 2}, database.api_key)
        unknown = call(app, payload, "opk_unknown")
        assert (rejected.status_code, limited.status_code, unknown.status_code) == (422, 429, 401)
        assert int(limited.headers["retry-after"]) >= 1
    finally:
        serving.dispose()
    logged = database.rows("SELECT member_id::text, request_id::text, item_count FROM evaluation_log")
    assert logged == [(database.member_id, request_id, 2)]
    assert database.rows("SELECT items_used FROM rate_limits") == [(2,)]
    everything = "".join(
        str(database.rows(f"SELECT * FROM {table}"))  # noqa: S608 - fixed table names of this test
        for table in ("api_credentials", "evaluation_log", "key_versions", "rate_limits")
    )
    for element in (*sent, *expected):
        raw = base64.b64decode(element)
        for spelling in (element, raw.hex(), repr(raw)):
            assert spelling not in everything
    assert scalar.hex() not in everything and base64.b64encode(scalar).decode() not in everything
