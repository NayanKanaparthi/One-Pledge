"""The composition root of outside records: the one place that wires implementations to ports."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from fastapi import FastAPI

from onepledge_outside_records.application.administration import RegisterClient, SeedRecords
from onepledge_outside_records.infrastructure.csv_source import CsvRecordsSource
from onepledge_outside_records.infrastructure.db.engines import DatabaseTarget, EngineSet
from onepledge_outside_records.infrastructure.db.migrations import MigrationRunner
from onepledge_outside_records.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_outside_records.infrastructure.db.repositories import (
    SqlClientRegistry,
    SqlRecordsReader,
    SqlRecordsWriter,
)
from onepledge_outside_records.infrastructure.settings import AdminSettings, ServeSettings
from onepledge_outside_records.interface.http.app import create_app


class Composition:
    """Builds the collaborators of one command from its settings, and disposes the engines it made."""

    def __init__(self) -> None:
        self._engines = EngineSet()

    def __enter__(self) -> Self:
        """Use as a context manager so every engine is disposed."""
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        """Dispose every engine made by this composition."""
        self._engines.dispose()

    @staticmethod
    def provisioner(settings: AdminSettings) -> tuple[DatabaseProvisioner, DatabaseTarget]:
        """The superuser provisioner and the database it provisions."""
        target = DatabaseTarget.from_urls(settings.require_migration_url(), settings.require_database_url())
        return DatabaseProvisioner(settings.require_admin_url()), target

    @staticmethod
    def migrations(settings: AdminSettings) -> MigrationRunner:
        """Alembic as the owner role."""
        return MigrationRunner(settings.require_migration_url(), settings.require_app_role())

    def seed_records(self, settings: AdminSettings) -> SeedRecords:
        """Seeding as the owner role."""
        engine = self._engines.engine(settings.require_migration_url())
        return SeedRecords(CsvRecordsSource(), SqlRecordsWriter(engine))

    def register_client(self, settings: AdminSettings) -> RegisterClient:
        """Client registration as the owner role."""
        return RegisterClient(SqlClientRegistry(self._engines.engine(settings.require_migration_url())))

    def service(self, settings: ServeSettings) -> FastAPI:
        """The API on the read-only app role, after checking the schema is in place."""
        reader = SqlRecordsReader(self._engines.engine(settings.require_database_url()))
        reader.ensure_readable()
        return create_app(reader)
