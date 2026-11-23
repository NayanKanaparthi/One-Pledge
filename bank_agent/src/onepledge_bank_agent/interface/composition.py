"""The composition root of the bank agent: the one place that wires implementations to ports.

Use cases receive every collaborator through their constructor; nothing else in the
code base builds an engine, an HTTP client or a repository. There are two roots, one
per settings scope: `AdminComposition` for the administrative commands (owner and
superuser URLs) and `RuntimeComposition` for the processes that handle tapes, which
never see an owner credential. Stages are registered with `AdvanceSubmission` in
`RuntimeComposition.stages`, in pipeline order; the registry outbox sync is registered
with the worker as its periodic task.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from types import TracebackType
from typing import Self

from onepledge_bank_agent.application.advance_submission import AdvanceSubmission, Stage
from onepledge_bank_agent.application.alert_maker import AlertMaker
from onepledge_bank_agent.application.authorisation import Authoriser
from onepledge_bank_agent.application.clean_tape import CleanTape
from onepledge_bank_agent.application.collect_certificates import CollectCertificates
from onepledge_bank_agent.application.create_release_instruction import CreateReleaseInstruction
from onepledge_bank_agent.application.faults import FaultInjector, NoFaults
from onepledge_bank_agent.application.get_integrity_report import GetIntegrityReport
from onepledge_bank_agent.application.ingest_tape import IngestTape
from onepledge_bank_agent.application.outbox_handlers import AlertMessages, CaseUpdateMessages
from onepledge_bank_agent.application.ports import Clock, IdGenerator
from onepledge_bank_agent.application.record_schema_migration import RecordSchemaMigration
from onepledge_bank_agent.application.record_startup import RecordStartupConfiguration
from onepledge_bank_agent.application.registry_sender import RegistrySender
from onepledge_bank_agent.application.release_loans import ReleaseLoans
from onepledge_bank_agent.application.resubmit_loan import ResubmitLoan, ResubmitParts
from onepledge_bank_agent.application.result_application import ResultApplier
from onepledge_bank_agent.application.retry_tape import RetryTape
from onepledge_bank_agent.application.review_alert import ReviewAlert, ReviewParts
from onepledge_bank_agent.application.run_deep_verification import RunDeepVerification, VerificationSources
from onepledge_bank_agent.application.run_local_checks import OutsideRecordsClients, RunLocalChecks
from onepledge_bank_agent.application.run_pipeline_worker import RunPipelineWorker, WorkerSettings
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.application.seed_reference import SeedReferenceData
from onepledge_bank_agent.application.submit_pledge import SubmitPledge
from onepledge_bank_agent.application.submit_stage import HaltRule, SubmissionUseCase, SubmitStage
from onepledge_bank_agent.application.sync_registry_outbox import (
    OutboxHandlers,
    OutboxSource,
    OutboxTiming,
    SyncRegistryOutbox,
)
from onepledge_bank_agent.application.tape_requests import SubmitSettings, TapeRequests
from onepledge_bank_agent.application.tokenize_loans import KeyHolders, TokenizeLoans
from onepledge_bank_agent.application.verify_certificate import VerifyCertificate
from onepledge_bank_agent.application.verify_checkpoint import EventHeaderReader, VerifyCheckpoint
from onepledge_bank_agent.domain.access_policy import AccessPolicy
from onepledge_bank_agent.domain.checks import CheckWindows, LocalCheckRules
from onepledge_bank_agent.domain.enums import TapeKind
from onepledge_bank_agent.domain.explanation import TemplateAlertExplainer
from onepledge_bank_agent.domain.tape_file import TapeFileReader
from onepledge_bank_agent.infrastructure.audit import HashChainedAuditWriter
from onepledge_bank_agent.infrastructure.clock import SystemClock
from onepledge_bank_agent.infrastructure.crypto.blinder import OprfBlinder
from onepledge_bank_agent.infrastructure.crypto.signatures import Ed25519SignatureVerifier
from onepledge_bank_agent.infrastructure.db.controls import SqlDatabaseControlInspector
from onepledge_bank_agent.infrastructure.db.engines import DatabaseTarget, EngineSet
from onepledge_bank_agent.infrastructure.db.locks import SqlOutboxLock, SqlSubmissionLocks
from onepledge_bank_agent.infrastructure.db.migrations import MigrationRunner, SchemaInspector, head_revision
from onepledge_bank_agent.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_bank_agent.infrastructure.db.role_guard import OwnerRoleGuard, RuntimeRoleGuard
from onepledge_bank_agent.infrastructure.db.session import SqlUnitOfWorkFactory
from onepledge_bank_agent.infrastructure.http.base import HttpClientFactory, make_http_client
from onepledge_bank_agent.infrastructure.http.clients import ClientParts, ServiceClients
from onepledge_bank_agent.infrastructure.ids import RandomIds
from onepledge_bank_agent.infrastructure.logging import StructlogOperationalLog
from onepledge_bank_agent.infrastructure.seed_reference_file import ReferenceFileSource
from onepledge_bank_agent.infrastructure.settings import AdminSettings, RuntimeSettings
from onepledge_bank_agent.infrastructure.stop_signal import EventStopSignal

VIA_CLI = "cli"


@dataclass(frozen=True)
class Collaborators:
    """The process level collaborators. The defaults are the production ones."""

    clock: Clock = field(default_factory=SystemClock)
    ids: IdGenerator = field(default_factory=RandomIds)
    faults: FaultInjector = field(default_factory=NoFaults)
    http_client_factory: HttpClientFactory = make_http_client
    sleep: Callable[[float], None] = time.sleep
    stop: EventStopSignal = field(default_factory=EventStopSignal)


class _Resources:
    """Engines, HTTP callers and the shared runtime of one command; closed together on exit."""

    def __init__(self, collaborators: Collaborators | None) -> None:
        self._parts = collaborators if collaborators is not None else Collaborators()
        self._engines = EngineSet()
        log = StructlogOperationalLog("onepledge_bank_agent")
        self._runtime = Runtime(self._parts.clock, self._parts.ids, self._parts.faults, log, self._parts.stop)
        self._writer = HashChainedAuditWriter(self._parts.clock, self._parts.ids)

    def __enter__(self) -> Self:
        """Use as a context manager so every engine and client is closed."""
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        """Dispose every engine."""
        self._engines.dispose()

    def _units(self, url: str) -> SqlUnitOfWorkFactory:
        return SqlUnitOfWorkFactory(self._engines.engine(url), self._writer, self._parts.ids)


class AdminComposition(_Resources):
    """`db provision`, `db migrate`, `db status` and `seed-reference`."""

    def __init__(self, settings: AdminSettings, collaborators: Collaborators | None = None) -> None:
        super().__init__(collaborators)
        self._settings = settings

    def provisioner(self) -> tuple[DatabaseProvisioner, DatabaseTarget]:
        """The superuser provisioner and the database and roles it provisions."""
        target = DatabaseTarget.from_urls(
            self._settings.require_migration_url(), self._settings.require_database_url()
        )
        return DatabaseProvisioner(self._settings.require_admin_url()), target

    def owner_guard(self) -> OwnerRoleGuard:
        """The mirror check of `db migrate` and `seed-reference` (SPEC_DECISIONS 72)."""
        return OwnerRoleGuard(self._engines.engine(self._settings.require_migration_url()))

    def migrations(self) -> MigrationRunner:
        """Alembic as the owner role."""
        return MigrationRunner(
            self._settings.require_migration_url(), self._settings.require_app_role(), self._runtime.log
        )

    def record_schema_migration(self) -> RecordSchemaMigration:
        """The `SCHEMA_MIGRATED` audit entry, written as the owner role."""
        return RecordSchemaMigration(
            self._units(self._settings.require_migration_url()), VIA_CLI, self._runtime.log
        )

    def schema(self) -> SchemaInspector:
        """The schema revision, read as the app role."""
        return SchemaInspector(self._engines.engine(self._settings.require_database_url()))

    def seed_reference(self) -> SeedReferenceData:
        """Reference seeding as the owner role."""
        units = self._units(self._settings.require_migration_url())
        return SeedReferenceData(units, ReferenceFileSource(), VIA_CLI)


class RuntimeComposition(_Resources):
    """Intake, the pipeline worker and the registry side use cases, on the app role only."""

    def __init__(self, settings: RuntimeSettings, collaborators: Collaborators | None = None) -> None:
        super().__init__(collaborators)
        self._settings = settings
        parts = ClientParts(
            self._parts.http_client_factory, self._runtime.log, self._parts.sleep, self._parts.ids
        )
        self._clients = ServiceClients(settings, parts)

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        """Close every HTTP client, then dispose every engine."""
        self._clients.close()
        super().__exit__(kind, error, trace)

    def schema(self) -> SchemaInspector:
        """The schema revision, read as the app role."""
        return SchemaInspector(self._engines.engine(self._settings.require_database_url()))

    def role_guard(self) -> RuntimeRoleGuard:
        """The check that the runtime role holds the app role's grants and nothing more."""
        return RuntimeRoleGuard(self._engines.engine(self._settings.require_database_url(), inspecting=True))

    def ingest_tape(self) -> IngestTape:
        """Intake as the app role."""
        return IngestTape(self._app_units(), TapeFileReader(), AccessPolicy(), self._runtime)

    def stages(self) -> tuple[Stage, ...]:
        """The registered pipeline stages, in order."""
        units, tunables = self._app_units(), self._settings.tunables
        windows = CheckWindows(
            title_pending_window_days=tunables.title_pending_window_days,
            first_payment_grace_days=tunables.first_payment_grace_days,
            payment_window_days=tunables.payment_window_days,
            contract_date_tolerance_days=tunables.contract_date_tolerance_days,
        )
        outside = self._clients.outside_records()
        sources = OutsideRecordsClients(titles=outside, payments=outside, contracts=outside)
        holders = KeyHolders(self._clients.key_holder(1), self._clients.key_holder(2))
        requests = TapeRequests(units, self._sender(), self._runtime, self._submit_settings())
        by_kind: dict[TapeKind, SubmissionUseCase] = {
            TapeKind.BACK_BOOK: SubmitPledge(requests),
            TapeKind.PLEDGE: SubmitPledge(requests),
            TapeKind.RELEASE: ReleaseLoans(requests),
        }
        halt = self._halt_rule()
        return (
            CleanTape(units, self._runtime),
            RunLocalChecks(
                units, sources, LocalCheckRules(windows), self._runtime, tunables.check_chunk_loans
            ),
            TokenizeLoans(units, holders, OprfBlinder(), self._runtime, tunables.tokenize_chunk_loans),
            SubmitStage(units, halt, by_kind),
            CollectCertificates(units, self._verify_certificate(), self.outbox_sync(), self._runtime),
        )

    def advance_submission(self) -> AdvanceSubmission:
        """Stage dispatch with the failure handling of SPEC 13.7."""
        return AdvanceSubmission(
            self._app_units(), self.stages(), self._runtime, self._settings.tunables.pipeline_max_attempts
        )

    def stop_signal(self) -> EventStopSignal:
        """The process stop signal every stage of this composition checks after each unit."""
        return self._parts.stop

    def worker(self) -> RunPipelineWorker:
        """The pipeline worker on the app role, with the registry outbox sync as its periodic task."""
        settings = WorkerSettings(
            self._settings.tunables.pipeline_poll_seconds, (self.outbox_sync(),), halt=self._halt_rule()
        )
        return RunPipelineWorker(
            self._app_units(),
            self.advance_submission(),
            SqlSubmissionLocks(self._engines.engine(self._settings.require_database_url())),
            self._runtime,
            settings,
        )

    def outbox_sync(self) -> SyncRegistryOutbox:
        """The registry outbox sync, under the `onepledge:outbox` advisory lock."""
        units = self._app_units()
        verify = VerifyCheckpoint(units, self._headers(), self._verifier(), self._runtime)
        handlers = OutboxHandlers(
            AlertMessages(self._alert_maker()), CaseUpdateMessages(self._runtime.log), verify
        )
        source = OutboxSource(
            self._clients.registry(),
            SqlOutboxLock(self._engines.engine(self._settings.require_database_url()), self._parts.stop),
        )
        timing = OutboxTiming(self._settings.tunables.outbox_poll_seconds, self._blind_after())
        return SyncRegistryOutbox(units, source, handlers, self._runtime, timing)

    def review_alert(self) -> ReviewAlert:
        """Start a review or record a position."""
        parts = ReviewParts(
            self._clients.registry(), self._authoriser(), self.outbox_sync(), self._halt_rule()
        )
        return ReviewAlert(self._app_units(), parts, self._runtime)

    def resubmit_loan(self) -> ResubmitLoan:
        """Resubmit a cleared CONFLICT loan."""
        member_id = self._settings.registry.require_member_id()
        parts = ResubmitParts(
            self._sender(), self._verify_certificate(), self._authoriser(), member_id, self._halt_rule()
        )
        return ResubmitLoan(self._app_units(), parts, self._runtime)

    def create_release(self) -> CreateReleaseInstruction:
        """Release locked loans by id."""
        return CreateReleaseInstruction(self._app_units(), self._authoriser(), self._runtime)

    def retry_tape(self) -> RetryTape:
        """Resume a FAILED submission."""
        return RetryTape(self._app_units(), self._authoriser(), self._runtime)

    def deep_verification(self) -> RunDeepVerification:
        """Recompute every proof and store the report."""
        sources = VerificationSources(
            self._headers(), self._verifier(), self._controls(), self._blind_after()
        )
        return RunDeepVerification(self._app_units(), sources, self._runtime)

    def integrity_report(self) -> GetIntegrityReport:
        """The live integrity report."""
        return GetIntegrityReport(self._app_units(), self._controls(), self._runtime, self._blind_after())

    def startup_record(self) -> RecordStartupConfiguration:
        """The audit of a process starting with the halt rule off."""
        halt = self._settings.tunables.halt_on_registry_integrity_failure
        return RecordStartupConfiguration(self._app_units(), halt, self._runtime)

    def _blind_after(self) -> timedelta:
        """`OUTBOX_BLIND_HALT_SECONDS` (SPEC_DECISIONS 101)."""
        return timedelta(seconds=self._settings.tunables.outbox_blind_halt_seconds)

    def _halt_rule(self) -> HaltRule:
        enabled = self._settings.tunables.halt_on_registry_integrity_failure
        return HaltRule(self._app_units(), enabled, self._runtime.log, self.outbox_sync())

    def _app_units(self) -> SqlUnitOfWorkFactory:
        return self._units(self._settings.require_database_url())

    def _submit_settings(self) -> SubmitSettings:
        member_id = self._settings.registry.require_member_id()
        return SubmitSettings(member_id, self._settings.tunables.submit_batch_items)

    def _verifier(self) -> Ed25519SignatureVerifier:
        return Ed25519SignatureVerifier(self._settings.registry.require_public_keys())

    def _headers(self) -> EventHeaderReader:
        return EventHeaderReader(self._clients.registry())

    def _alert_maker(self) -> AlertMaker:
        return AlertMaker(TemplateAlertExplainer(), self._runtime)

    def _sender(self) -> RegistrySender:
        applier = ResultApplier(self._alert_maker(), self._runtime)
        return RegistrySender(self._app_units(), self._clients.registry(), applier, self._runtime)

    def _verify_certificate(self) -> VerifyCertificate:
        member_id = self._settings.registry.require_member_id()
        return VerifyCertificate(
            self._app_units(), self._clients.registry(), self._verifier(), member_id, self._runtime
        )

    def _authoriser(self) -> Authoriser:
        return Authoriser(self._app_units(), AccessPolicy(), self._runtime.log)

    def _controls(self) -> SqlDatabaseControlInspector:
        return SqlDatabaseControlInspector(
            self._engines.engine(self._settings.require_database_url(), inspecting=True), head_revision()
        )
