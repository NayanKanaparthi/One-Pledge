"""The composition root of outside records: the one place that wires implementations to ports."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from onepledge_outside_records.application.administration import RegisterClient, SeedRecords
from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.csv_source import CsvRecordsSource
from onepledge_outside_records.infrastructure.db.engines import DatabaseTarget, make_engine
from onepledge_outside_records.infrastructure.db.migrations import MigrationRunner
from onepledge_outside_records.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_outside_records.infrastructure.db.repositories import (
    SqlClientRegistry,
    SqlRecordsReader,
    SqlRecordsWriter,
)
from onepledge_outside_records.infrastructure.settings import Settings
from onepledge_outside_records.interface.http.app import create_app


class Composition:
    """Builds every collaborator a command needs from the settings, and disposes the engines it made."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engines: list[Engine] = []

    def __enter__(self) -> Self:
        """Use as a context manager so every engine is disposed."""
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        """Dispose every engine made by this composition."""
        for engine in self._engines:
            engine.dispose()
        self._engines.clear()

    def _engine(self, url: str) -> Engine:
        engine = make_engine(url)
        self._engines.append(engine)
        return engine

    def provisioner(self) -> tuple[DatabaseProvisioner, DatabaseTarget]:
        """The superuser provisioner and the database it provisions."""
        target = DatabaseTarget.from_urls(
            self._settings.require_migration_url(), self._settings.require_database_url()
        )
        return DatabaseProvisioner(self._settings.require_admin_url()), target

    def migrations(self) -> MigrationRunner:
        """Alembic as the owner role."""
        return MigrationRunner(self._settings.require_migration_url(), self._settings.require_app_role())

    def seed_records(self) -> SeedRecords:
        """Seeding as the owner role."""
        engine = self._engine(self._settings.require_migration_url())
        return SeedRecords(CsvRecordsSource(), SqlRecordsWriter(engine))

    def register_client(self) -> RegisterClient:
        """Client registration as the owner role."""
        return RegisterClient(SqlClientRegistry(self._engine(self._settings.require_migration_url())))

    def service(self) -> FastAPI:
        """The API on the read-only app role, after checking the schema is in place."""
        engine = self._engine(self._settings.require_database_url())
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1 FROM api_clients LIMIT 1"))
        except DBAPIError:
            raise OperatorError(
                "The database at DATABASE_URL cannot be read; run db provision and db migrate first."
            ) from None
        return create_app(SqlRecordsReader(engine))
