"""Shared fixtures: a real PostgreSQL database per session, seeded from the committed example records.

Databases and roles are created through the package's own provisioning code with a
unique name, migrated with its own migration runner, and dropped afterwards.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine
from sqlalchemy.engine import make_url

from onepledge_outside_records.application.administration import RegisterClient, SeedRecords
from onepledge_outside_records.infrastructure.csv_source import CsvRecordsSource
from onepledge_outside_records.infrastructure.db.engines import DatabaseTarget, make_engine
from onepledge_outside_records.infrastructure.db.migrations import MigrationRunner
from onepledge_outside_records.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_outside_records.infrastructure.db.repositories import (
    SqlClientRegistry,
    SqlRecordsReader,
    SqlRecordsWriter,
)
from onepledge_outside_records.interface.http.app import create_app

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACT = REPOSITORY / "contracts" / "outside_records.yaml"
EXAMPLE_RECORDS = REPOSITORY / "contracts" / "examples" / "tapes" / "example_outside_records"
ADMIN_URL = os.environ.get("PG_ADMIN_URL", "postgresql://postgres@localhost:55432/postgres")


@dataclass(frozen=True)
class Database:
    """One test database: its URLs, its app role and its provisioning target."""

    admin_url: str
    owner_url: str
    app_url: str
    app_role: str
    target: DatabaseTarget

    def environment(self, **extra: str) -> dict[str, str]:
        """The variables the command line reads for this database."""
        return {
            "PG_ADMIN_URL": self.admin_url,
            "MIGRATION_DATABASE_URL": self.owner_url,
            "DATABASE_URL": self.app_url,
            "DB_APP_ROLE": self.app_role,
            **extra,
        }


def new_database() -> Database:
    """Names and passwords for a fresh database. Nothing is created yet."""
    name = f"optest_{secrets.token_hex(5)}_outside"
    admin = make_url(ADMIN_URL)
    server = f"{admin.host or 'localhost'}:{admin.port or 5432}"
    owner_url = f"postgresql://{name}_owner:{secrets.token_urlsafe(18)}@{server}/{name}"
    app_url = f"postgresql://{name}_app:{secrets.token_urlsafe(18)}@{server}/{name}"
    return Database(
        ADMIN_URL, owner_url, app_url, f"{name}_app", DatabaseTarget.from_urls(owner_url, app_url)
    )


def new_api_key() -> str:
    """A client key in the SPEC 4 format."""
    return "opk_" + secrets.token_urlsafe(32)


def copy_example_records(directory: Path) -> Path:
    """A data directory holding the committed example outside records."""
    target = directory / "outside_records"
    target.mkdir(parents=True)
    for name in ("titles.csv", "payments.csv", "contracts.csv"):
        shutil.copyfile(EXAMPLE_RECORDS / name, target / name)
    return directory


@pytest.fixture(scope="session")
def example_records() -> Path:
    """The committed example outside records (`contracts/examples/tapes/example_outside_records`)."""
    return EXAMPLE_RECORDS


@pytest.fixture(scope="session")
def contract_path() -> Path:
    """`contracts/outside_records.yaml`."""
    return CONTRACT


@pytest.fixture(scope="session")
def copy_records() -> Callable[[Path], Path]:
    """Writes a data directory with the example records into a directory."""
    return copy_example_records


@pytest.fixture(scope="session")
def make_key() -> Callable[[], str]:
    """Makes client keys in the SPEC 4 format."""
    return new_api_key


@pytest.fixture
def fresh_database() -> Iterator[Database]:
    """A database that is not provisioned yet; dropped (with its roles) after the test."""
    database = new_database()
    yield database
    DatabaseProvisioner(ADMIN_URL).drop(database.target)


@pytest.fixture
def provisioned_database(fresh_database: Database) -> Database:
    """A provisioned but not migrated database."""
    DatabaseProvisioner(ADMIN_URL).provision(fresh_database.target)
    return fresh_database


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    """The session's provisioned and migrated database."""
    found = new_database()
    provisioner = DatabaseProvisioner(ADMIN_URL)
    provisioner.provision(found.target)
    try:
        MigrationRunner(found.owner_url, found.app_role).upgrade()
        yield found
    finally:
        provisioner.drop(found.target)


@pytest.fixture(scope="session")
def example_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A data directory with the committed example records."""
    return copy_example_records(tmp_path_factory.mktemp("example"))


@pytest.fixture(scope="session")
def owner_engine(database: Database) -> Iterator[Engine]:
    """An engine on the owner role."""
    engine = make_engine(database.owner_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(database: Database) -> Iterator[Engine]:
    """An engine on the read-only app role."""
    engine = make_engine(database.app_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def api_key(owner_engine: Engine, example_dir: Path) -> str:
    """Seeds the session database from the example records and registers one client; returns its key."""
    SeedRecords(CsvRecordsSource(), SqlRecordsWriter(owner_engine)).run(example_dir)
    key = new_api_key()
    RegisterClient(SqlClientRegistry(owner_engine)).run("harbor", key)
    return key


class InProcessApi:
    """Calls the ASGI app in-process through httpx (no network, no server)."""

    def __init__(self, app: FastAPI) -> None:
        self._runner = asyncio.Runner()
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        self._client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    def request(self, method: str, url: str, headers: Mapping[str, str] | None = None) -> httpx.Response:
        """One request; the query string goes in `url`."""
        return self._runner.run(self._client.request(method, url, headers=headers))

    def close(self) -> None:
        """Close the client and the event loop."""
        self._runner.run(self._client.aclose())
        self._runner.close()


@pytest.fixture
def call(app_engine: Engine, api_key: str) -> Iterator[Callable[..., httpx.Response]]:
    """`call(method, url, headers=None)` against the app on the seeded session database."""
    api = InProcessApi(create_app(SqlRecordsReader(app_engine)))
    yield api.request
    api.close()


@pytest.fixture
def call_app() -> Iterator[Callable[[FastAPI], InProcessApi]]:
    """Builds in-process clients for apps made inside a test, and closes them afterwards."""
    made: list[InProcessApi] = []

    def build(app: FastAPI) -> InProcessApi:
        made.append(InProcessApi(app))
        return made[-1]

    yield build
    for api in made:
        api.close()
