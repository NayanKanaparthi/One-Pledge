"""The composition root of the bank agent: the one place that wires implementations to ports.

Use cases receive every collaborator through their constructor; nothing else in the
code base builds an engine, an HTTP client or a repository. Stages are registered
with `AdvanceSubmission` here, in pipeline order.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

from onepledge_bank_agent.application.advance_submission import AdvanceSubmission, Stage
from onepledge_bank_agent.application.clean_tape import CleanTape
from onepledge_bank_agent.application.faults import FaultInjector, NoFaults
from onepledge_bank_agent.application.ingest_tape import IngestTape
from onepledge_bank_agent.application.ports import Clock, IdGenerator
from onepledge_bank_agent.application.run_local_checks import OutsideRecordsClients, RunLocalChecks
from onepledge_bank_agent.application.run_pipeline_worker import RunPipelineWorker, WorkerSettings
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.application.seed_reference import SeedReferenceData
from onepledge_bank_agent.domain.access_policy import AccessPolicy
from onepledge_bank_agent.domain.checks import CheckWindows, LocalCheckRules
from onepledge_bank_agent.domain.tape_file import TapeFileReader
from onepledge_bank_agent.infrastructure.audit import HashChainedAuditWriter
from onepledge_bank_agent.infrastructure.clock import SystemClock
from onepledge_bank_agent.infrastructure.db.engines import DatabaseTarget, EngineSet
from onepledge_bank_agent.infrastructure.db.locks import SqlSubmissionLocks
from onepledge_bank_agent.infrastructure.db.migrations import MigrationRunner, SchemaInspector
from onepledge_bank_agent.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_bank_agent.infrastructure.db.session import SqlUnitOfWorkFactory
from onepledge_bank_agent.infrastructure.http.base import (
    HttpClientFactory,
    HttpTimeouts,
    RetryingHttpCaller,
    make_http_client,
)
from onepledge_bank_agent.infrastructure.http.outside_records import OutsideRecordsClient
from onepledge_bank_agent.infrastructure.ids import RandomIds
from onepledge_bank_agent.infrastructure.logging import StructlogOperationalLog
from onepledge_bank_agent.infrastructure.seed_reference_file import ReferenceFileSource
from onepledge_bank_agent.infrastructure.settings import Settings

OUTSIDE_RECORDS = "outside_records"


@dataclass(frozen=True)
class Collaborators:
    """The process level collaborators. The defaults are the production ones."""

    clock: Clock = field(default_factory=SystemClock)
    ids: IdGenerator = field(default_factory=RandomIds)
    faults: FaultInjector = field(default_factory=NoFaults)
    http_client_factory: HttpClientFactory = make_http_client
    sleep: Callable[[float], None] = time.sleep


class Composition:
    """Builds what a command needs from the settings, and disposes every engine and client it made."""

    def __init__(self, settings: Settings, collaborators: Collaborators | None = None) -> None:
        self._settings = settings
        self._parts = collaborators if collaborators is not None else Collaborators()
        self._engines = EngineSet()
        self._callers: list[RetryingHttpCaller] = []
        log = StructlogOperationalLog("onepledge_bank_agent")
        self._runtime = Runtime(self._parts.clock, self._parts.ids, self._parts.faults, log)
        self._writer = HashChainedAuditWriter(self._parts.clock, self._parts.ids)

    def __enter__(self) -> Self:
        """Use as a context manager so every engine and client is closed."""
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        """Close every client and dispose every engine."""
        for caller in self._callers:
            caller.close()
        self._callers.clear()
        self._engines.dispose()

    def provisioner(self) -> tuple[DatabaseProvisioner, DatabaseTarget]:
        """The superuser provisioner and the database and roles it provisions."""
        target = DatabaseTarget.from_urls(
            self._settings.require_migration_url(), self._settings.require_database_url()
        )
        return DatabaseProvisioner(self._settings.require_admin_url()), target

    def migrations(self) -> MigrationRunner:
        """Alembic as the owner role."""
        return MigrationRunner(self._settings.require_migration_url(), self._settings.require_app_role())

    def schema(self) -> SchemaInspector:
        """The schema revision, read as the app role."""
        return SchemaInspector(self._engines.engine(self._settings.require_database_url()))

    def seed_reference(self) -> SeedReferenceData:
        """Reference seeding as the owner role."""
        engine = self._engines.engine(self._settings.require_migration_url())
        return SeedReferenceData(
            SqlUnitOfWorkFactory(engine, self._writer, self._parts.ids), ReferenceFileSource()
        )

    def ingest_tape(self) -> IngestTape:
        """Intake as the app role."""
        return IngestTape(self._app_units(), TapeFileReader(), AccessPolicy(), self._runtime)

    def stages(self) -> tuple[Stage, ...]:
        """The registered pipeline stages, in order."""
        units = self._app_units()
        tunables = self._settings.tunables
        windows = CheckWindows(
            title_pending_window_days=tunables.title_pending_window_days,
            first_payment_grace_days=tunables.first_payment_grace_days,
            payment_window_days=tunables.payment_window_days,
            contract_date_tolerance_days=tunables.contract_date_tolerance_days,
        )
        outside = self._outside_records()
        sources = OutsideRecordsClients(titles=outside, payments=outside, contracts=outside)
        return (
            CleanTape(units, self._runtime),
            RunLocalChecks(
                units, sources, LocalCheckRules(windows), self._runtime, tunables.check_chunk_loans
            ),
        )

    def advance_submission(self) -> AdvanceSubmission:
        """Stage dispatch with the failure handling of SPEC 13.7."""
        return AdvanceSubmission(
            self._app_units(), self.stages(), self._runtime, self._settings.tunables.pipeline_max_attempts
        )

    def worker(self) -> RunPipelineWorker:
        """The pipeline worker on the app role."""
        engine = self._engines.engine(self._settings.require_database_url())
        return RunPipelineWorker(
            self._app_units(),
            self.advance_submission(),
            SqlSubmissionLocks(engine),
            self._runtime,
            WorkerSettings(poll_seconds=self._settings.tunables.pipeline_poll_seconds),
        )

    def _app_units(self) -> SqlUnitOfWorkFactory:
        engine = self._engines.engine(self._settings.require_database_url())
        return SqlUnitOfWorkFactory(engine, self._writer, self._parts.ids)

    def _outside_records(self) -> OutsideRecordsClient:
        url, key = self._settings.require_outside_records()
        tunables = self._settings.tunables
        timeouts = HttpTimeouts(tunables.http_connect_timeout_seconds, tunables.http_read_timeout_seconds)
        caller = RetryingHttpCaller(
            self._parts.http_client_factory(url, timeouts),
            key,
            OUTSIDE_RECORDS,
            self._runtime.log,
            self._parts.sleep,
        )
        self._callers.append(caller)
        return OutsideRecordsClient(caller)
