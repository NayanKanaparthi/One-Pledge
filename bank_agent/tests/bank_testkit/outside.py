"""Outside records for tests: the real service in-process, or every answer from one handler."""

from __future__ import annotations

import secrets
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from bank_testkit.database import ADMIN_URL
from onepledge_bank_agent.infrastructure.http.base import HttpTimeouts
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
from onepledge_testtools.scratch_databases import deferred_signals, new_name

OUTSIDE_BASE_URL = "http://outside-records.test"


class OutsideService(Protocol):
    """What the harness needs from an outside records stand-in: its key and in-process clients."""

    api_key: str

    def client_factory(self) -> Callable[[str, HttpTimeouts], httpx.Client]:
        """Clients that reach the stand-in."""
        ...


class ScriptedOutsideRecords:
    """An outside records stand-in whose every answer comes from one handler."""

    api_key = "opk_scripted"

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    def client_factory(self) -> Callable[[str, HttpTimeouts], httpx.Client]:
        """Clients answered by the handler."""

        def build(base_url: str, timeouts: HttpTimeouts) -> httpx.Client:
            return httpx.Client(base_url=base_url, transport=httpx.MockTransport(self._handler))

        return build


class InProcessOutsideRecords:
    """The real outside records service on its own test database, called in-process through httpx.

    `down = True` makes every call fail at the transport level, as if the service were stopped.
    """

    def __init__(self, data_dir: Path) -> None:
        name = new_name("outside")
        admin = make_url(ADMIN_URL)
        server = f"{admin.host or 'localhost'}:{admin.port or 5432}"
        owner = f"postgresql://{name}_owner:{secrets.token_urlsafe(18)}@{server}/{name}"
        app = f"postgresql://{name}_app:{secrets.token_urlsafe(18)}@{server}/{name}"
        self._target = DatabaseTarget.from_urls(owner, app)
        with deferred_signals():  # SPEC_DECISIONS 115: the object being made is known and dropped
            DatabaseProvisioner(ADMIN_URL).provision(self._target)
            MigrationRunner(owner, f"{name}_app").upgrade()
        self._owner_engine = make_engine(owner)
        self._app_engine = make_engine(app)
        SeedRecords(CsvRecordsSource(), SqlRecordsWriter(self._owner_engine)).run(data_dir)
        self.api_key = "opk_" + secrets.token_urlsafe(32)
        RegisterClient(SqlClientRegistry(self._owner_engine)).run("harbor", self.api_key)
        self._client = TestClient(create_app(SqlRecordsReader(self._app_engine)), base_url=OUTSIDE_BASE_URL)
        self.down = False
        self.requests = 0

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.down:
            raise httpx.ConnectError("outside records is stopped", request=request)
        answer = self._client.request(
            request.method, str(request.url), headers=dict(request.headers), content=request.read()
        )
        return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)

    def client_factory(self) -> Callable[[str, HttpTimeouts], httpx.Client]:
        """An `HttpClientFactory` whose clients reach this service in-process."""

        def build(base_url: str, timeouts: HttpTimeouts) -> httpx.Client:
            return httpx.Client(base_url=base_url, transport=httpx.MockTransport(self._handle))

        return build

    def close(self) -> None:
        """Dispose the engines and drop the database."""
        self._client.close()
        self._owner_engine.dispose()
        self._app_engine.dispose()
        DatabaseProvisioner(ADMIN_URL).drop(self._target)


def copy_outside_records(source: Path, target: Path) -> Path:
    """A data directory holding the three outside records files of `source`."""
    (target / "outside_records").mkdir(parents=True, exist_ok=True)
    for name in ("titles.csv", "payments.csv", "contracts.csv"):
        shutil.copyfile(source / name, target / "outside_records" / name)
    return target
