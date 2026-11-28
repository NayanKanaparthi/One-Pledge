"""Shared fixtures of the key holder tests.

Unit tests get a whole holder in memory (`make_holder`): the real use cases, the real
`oprf` evaluation and the real FastAPI app, with a journal and a credential directory held
in lists. Integration tests get a real PostgreSQL database per test (`database`), created
through the package's own provisioning, migration and registration code and dropped
afterwards.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import oprf
import psycopg
import pytest
import structlog
from fastapi import FastAPI
from onepledge_key_holder.application.authenticate_member import AuthenticateMember
from onepledge_key_holder.application.evaluate_blinded import EvaluateBlinded, HolderKeys
from onepledge_key_holder.application.report_health import ReportHealth
from onepledge_key_holder.domain.evaluation import EvaluationRecord, EvaluationRequest
from onepledge_key_holder.domain.identity import HolderId, KeyVersion
from onepledge_key_holder.infrastructure.db.engines import DatabaseTarget
from onepledge_key_holder.infrastructure.db.migrations import MigrationRunner
from onepledge_key_holder.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_key_holder.infrastructure.db.registration import ConfigurationRegistrar
from onepledge_key_holder.infrastructure.scalar_keyring import ScalarKeyring
from onepledge_key_holder.interface.http import UseCases, create_app
from sqlalchemy.engine import make_url

from onepledge_testtools.scratch_databases import admin_url, deferred_signals, new_name

START = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
FIXED_REQUEST_ID = "983e2beb-2d83-4d32-a3b5-83a68e98f84a"


@pytest.fixture(autouse=True)
def _unit_isolation(enforce_unit_isolation: None) -> None:
    """Every test here marked `unit` has no network, database, child process or file write."""
    del enforce_unit_isolation


class FixedClock:
    """A clock that only moves when told to."""

    def __init__(self, start: datetime = START) -> None:
        self._now = start

    def now(self) -> datetime:
        """The current fixed time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self._now += timedelta(seconds=seconds)


class CountingIds:
    """Repeatable UUIDs from a counter."""

    def __init__(self) -> None:
        self._next = 1

    def new_id(self) -> uuid.UUID:
        """The next identifier."""
        value = self._next
        self._next += 1
        return uuid.UUID(int=value, version=4)


@dataclass
class MemoryJournal:
    """`EvaluationJournal` in lists, with the same rule as the SQL one: all or nothing."""

    records: list[EvaluationRecord] = field(default_factory=list)
    used: dict[tuple[str, datetime], int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def accept(self, record: EvaluationRecord, window_start: datetime, cap: int) -> bool:
        """Count and append, or refuse without writing."""
        with self.lock:
            key = (record.member_id, window_start)
            if self.used.get(key, 0) + record.item_count > cap:
                return False
            self.used[key] = self.used.get(key, 0) + record.item_count
            self.records.append(record)
            return True


class MemoryCredentials:
    """`CredentialDirectory` over a mapping of SHA-256 to member id."""

    def __init__(self, members: Mapping[str, str]) -> None:
        self._members = dict(members)

    def member_for(self, key_sha256: str) -> str | None:
        """The member of a known hash."""
        return self._members.get(key_sha256)


class InProcessApi:
    """Calls an ASGI app in-process through httpx (no network, no server)."""

    def __init__(self, app: FastAPI) -> None:
        self._runner = asyncio.Runner()
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        self._client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: object = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    ) -> httpx.Response:
        """One request. `json_body` is sent as `application/json`; pairs of headers may repeat a name."""
        pairs = list(headers.items()) if isinstance(headers, Mapping) else list(headers or [])
        if json_body is not None:
            content = json.dumps(json_body).encode("utf-8")
            pairs.append(("Content-Type", "application/json"))
        return self._runner.run(self._client.request(method, url, content=content, headers=pairs))

    def close(self) -> None:
        """Close the client and the event loop."""
        self._runner.run(self._client.aclose())
        self._runner.close()


def new_api_key() -> str:
    """A member key in the layer 1 format."""
    return "opk_" + secrets.token_urlsafe(32)


def sha256_hex(text: str) -> str:
    """SHA-256 hex, computed here and not by the code under test."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Harness:
    """One holder in memory: its identity, its scalar, one member and everything the tests inspect."""

    holder_id: HolderId
    version: KeyVersion
    scalar: bytes
    member_id: str
    api_key: str
    clock: FixedClock
    journal: MemoryJournal
    use_cases: UseCases
    app: FastAPI
    api: InProcessApi

    def evaluate(self, elements: Sequence[str], **extra: object) -> httpx.Response:
        """`POST /v1/evaluate` as the harness's member, with a fresh request id."""
        body = {"request_id": str(uuid.uuid4()), "elements": list(elements), **extra}
        return self.post(body)

    def post(self, payload: object, **headers: str) -> httpx.Response:
        """`POST /v1/evaluate` with this JSON payload; `headers` are added to, or replace, the API key."""
        sent = {"X-API-Key": self.api_key, **headers}
        return self.api.request("POST", "/v1/evaluate", json_body=payload, headers=sent)

    def run(self, elements: Sequence[str], version: int | None = None) -> list[str]:
        """The use case itself, as the harness's member, with the fixed request id."""
        pinned = KeyVersion(version) if version is not None else None
        request = EvaluationRequest(FIXED_REQUEST_ID, pinned, tuple(elements))
        return list(self.use_cases.evaluate.run(self.member_id, request).elements)


def build_harness(holder_id: str, scalar: bytes | None, cap: int, version: int) -> Harness:
    """A holder with one member, a fixed clock and an in-memory journal."""
    holder, key_version = HolderId(holder_id), KeyVersion(version)
    secret = scalar if scalar is not None else bytes(oprf.mask.random())
    api_key, member_id = new_api_key(), str(uuid.uuid4())
    clock, journal = FixedClock(), MemoryJournal()
    use_cases = UseCases(
        report_health=ReportHealth(holder, key_version),
        authenticate=AuthenticateMember(MemoryCredentials({sha256_hex(api_key): member_id})),
        evaluate=EvaluateBlinded(
            HolderKeys(holder, key_version, ScalarKeyring({key_version: secret})),
            journal,
            clock,
            CountingIds(),
            cap,
        ),
    )
    app = create_app(use_cases, structlog.get_logger())
    return Harness(
        holder, key_version, secret, member_id, api_key, clock, journal, use_cases, app, InProcessApi(app)
    )


@pytest.fixture
def make_holder() -> Iterator[Callable[..., Harness]]:
    """Builds in-memory holders and closes their clients afterwards."""
    made: list[Harness] = []

    def build(
        holder_id: str = "kh1", scalar: bytes | None = None, cap: int = 1_000_000, version: int = 1
    ) -> Harness:
        made.append(build_harness(holder_id, scalar, cap, version))
        return made[-1]

    yield build
    for harness in made:
        harness.api.close()


@pytest.fixture(scope="session")
def point() -> Callable[[bytes], str]:
    """The base64 element of the ristretto255 point a seed hashes to."""

    def build(seed: bytes) -> str:
        return base64.b64encode(bytes(oprf.data.hash(seed))).decode("ascii")

    return build


@dataclass(frozen=True)
class ScratchDatabase:
    """One test database: its URLs, its roles, and the member registered in it."""

    admin_url: str
    owner_url: str
    app_url: str
    target: DatabaseTarget
    member_id: str
    api_key: str

    def environment(self, **extra: str) -> dict[str, str]:
        """The variables the command line reads for this database."""
        return {
            "PG_ADMIN_URL": self.admin_url,
            "MIGRATION_DATABASE_URL": self.owner_url,
            "DATABASE_URL": self.app_url,
            "MEMBER_KEY_HASHES": json.dumps({self.member_id: sha256_hex(self.api_key)}),
            **extra,
        }

    def rows(self, statement: str, role: str = "owner") -> list[tuple[Any, ...]]:
        """Every row of a query, run as the owner or the app role."""
        url = self.owner_url if role == "owner" else self.app_url
        with psycopg.connect(url) as connection:
            return connection.execute(statement.encode()).fetchall()

    def execute(self, statement: str, role: str = "owner") -> None:
        """One statement with no result, committed, as the owner or the app role."""
        url = self.owner_url if role == "owner" else self.app_url
        with psycopg.connect(url) as connection:
            connection.execute(statement.encode())


def new_database() -> ScratchDatabase:
    """Names and passwords for a fresh database. Nothing is created yet."""
    name = new_name("kh")
    admin = make_url(admin_url())
    server = f"{admin.host or 'localhost'}:{admin.port or 5432}"
    owner_url = f"postgresql://{name}_owner:{secrets.token_hex(12)}@{server}/{name}"
    app_url = f"postgresql://{name}_app:{secrets.token_hex(12)}@{server}/{name}"
    return ScratchDatabase(
        admin_url(),
        owner_url,
        app_url,
        DatabaseTarget.from_urls(owner_url, app_url),
        str(uuid.uuid4()),
        new_api_key(),
    )


@pytest.fixture
def make_fresh_database() -> Iterator[Callable[[], ScratchDatabase]]:
    """Databases that are not provisioned yet; each is dropped (with its roles) after the test."""
    made: list[ScratchDatabase] = []

    def build() -> ScratchDatabase:
        made.append(new_database())
        return made[-1]

    yield build
    for database in made:
        DatabaseProvisioner(database.admin_url).drop(database.target)


@pytest.fixture
def fresh_database(make_fresh_database: Callable[[], ScratchDatabase]) -> ScratchDatabase:
    """One database that is not provisioned yet."""
    return make_fresh_database()


@pytest.fixture
def make_database() -> Iterator[Callable[[], ScratchDatabase]]:
    """Provisioned and migrated databases with key version 1 active and one member; dropped afterwards."""
    made: list[ScratchDatabase] = []

    def build() -> ScratchDatabase:
        database = new_database()
        made.append(database)
        with deferred_signals():
            DatabaseProvisioner(database.admin_url).provision(database.target)
        MigrationRunner(database.owner_url).upgrade()
        ConfigurationRegistrar(database.owner_url).register(
            KeyVersion(1), {database.member_id: sha256_hex(database.api_key)}, datetime.now(UTC)
        )
        return database

    yield build
    for database in made:
        DatabaseProvisioner(database.admin_url).drop(database.target)


@pytest.fixture
def database(make_database: Callable[[], ScratchDatabase]) -> ScratchDatabase:
    """One provisioned and migrated database."""
    return make_database()
