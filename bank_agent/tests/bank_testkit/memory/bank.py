"""`MemoryBank`: one bank's use cases wired as the composition root wires them, over memory and fakes.

The graph mirrors `interface/composition.py` stage for stage, so a unit test drives the
same objects the worker runs, only with the in-memory unit of work, the scripted
registry, the scripted key holders, the fake blinder and signatures, and in-memory
outside records and locks. Nothing reaches a database, a network or a file.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from bank_testkit.memory.key_holders import FakeBlinder, ScriptedKeyHolder
from bank_testkit.memory.locks import MemoryOutboxLock, MemorySubmissionLocks
from bank_testkit.memory.outside_records import MemoryOutsideRecords
from bank_testkit.memory.registry import ScriptedRegistry
from bank_testkit.memory.signatures import FakeSignatures
from bank_testkit.memory.stores import MemoryControls
from bank_testkit.tapes import summit_row, summit_tape
from bank_testkit.world import World
from onepledge_bank_agent.application.advance_submission import AdvanceSubmission, StageRun
from onepledge_bank_agent.application.alert_maker import AlertMaker
from onepledge_bank_agent.application.authorisation import Authoriser
from onepledge_bank_agent.application.clean_tape import CleanTape
from onepledge_bank_agent.application.collect_certificates import CollectCertificates
from onepledge_bank_agent.application.create_release_instruction import (
    CreateReleaseInstruction,
    ReleaseCommand,
)
from onepledge_bank_agent.application.get_integrity_report import GetIntegrityReport
from onepledge_bank_agent.application.ingest_tape import IngestCommand, IngestTape
from onepledge_bank_agent.application.outbox_handlers import AlertMessages, CaseUpdateMessages
from onepledge_bank_agent.application.ports import PaymentsClient
from onepledge_bank_agent.application.record_startup import RecordStartupConfiguration
from onepledge_bank_agent.application.registry_sender import RegistrySender
from onepledge_bank_agent.application.release_loans import ReleaseLoans
from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.resubmit_loan import ResubmitCommand, ResubmitLoan, ResubmitParts
from onepledge_bank_agent.application.result_application import ResultApplier
from onepledge_bank_agent.application.retry_tape import RetryCommand, RetryTape
from onepledge_bank_agent.application.review_alert import (
    ReviewAction,
    ReviewAlert,
    ReviewCommand,
    ReviewParts,
)
from onepledge_bank_agent.application.run_deep_verification import RunDeepVerification, VerificationSources
from onepledge_bank_agent.application.run_local_checks import OutsideRecordsClients, RunLocalChecks
from onepledge_bank_agent.application.run_pipeline_worker import RunPipelineWorker, WorkerSettings
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
from onepledge_bank_agent.domain.alert import Alert
from onepledge_bank_agent.domain.audit import Actor
from onepledge_bank_agent.domain.checks import CheckWindows, LocalCheckRules
from onepledge_bank_agent.domain.enums import BankPosition, Role, TapeKind, TapeStatus
from onepledge_bank_agent.domain.explanation import TemplateAlertExplainer
from onepledge_bank_agent.domain.integrity import IntegrityReport
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.outside_records import ContractRecord, PaymentRecord, TitleRecord
from onepledge_bank_agent.domain.tape import TapeSubmission
from onepledge_bank_agent.domain.tape_file import TapeFileReader
from onepledge_bank_agent.domain.vin import check_digit

AS_OF = date(2026, 10, 15)
ORIGINATION = date(2025, 3, 15)
AMOUNT_CENTS = 2345017
_VIN_STEMS = "1HGCM826{}3A{:06d}"


def vins(count: int, start: int = 1) -> list[str]:
    """`count` distinct VINs with a valid check digit."""
    found = []
    for serial in range(start, start + count):
        stem = _VIN_STEMS.format("0", serial)
        found.append(stem[:8] + check_digit(stem) + stem[9:])
    return found


class MemoryBank:
    """A bank over a `World`: tapes go in through intake and move through the real stages."""

    def __init__(
        self,
        world: World | None = None,
        *,
        batch_items: int = 2,
        chunk_loans: int = 2,
        halt: bool = True,
        max_attempts: int = 3,
    ) -> None:
        self.world = world or World()
        self.member_id = uuid.UUID(int=0xBA, version=4)
        self.registry = ScriptedRegistry(self.member_id)
        self.holders = (ScriptedKeyHolder(1), ScriptedKeyHolder(2))
        self.blinder = FakeBlinder()
        self.signatures = FakeSignatures()
        self.outside = MemoryOutsideRecords()
        self.payments: PaymentsClient = self.outside
        self.controls = MemoryControls()
        self.locks = MemorySubmissionLocks()
        self.outbox_lock = MemoryOutboxLock()
        self.analyst = Actor.user(uuid.uuid4(), Role.ANALYST)
        self.officer = Actor.user(uuid.uuid4(), Role.CREDIT_OFFICER)
        self.batch_items, self.chunk_loans = batch_items, chunk_loans
        self.halt_enabled, self.max_attempts = halt, max_attempts
        self.blind_after_seconds = 300.0  # OUTBOX_BLIND_HALT_SECONDS

    # -- wiring, as interface/composition.py ---------------------------------------------------

    def halt_rule(self) -> HaltRule:
        """The halt rule of SPEC 15.4 and SPEC_DECISIONS 56."""
        return HaltRule(self.world.units, self.halt_enabled, self.world.log, self.outbox_sync())

    def authoriser(self) -> Authoriser:
        """The role checks of SPEC 16.3."""
        return Authoriser(self.world.units, AccessPolicy(), self.world.log)

    def alert_maker(self) -> AlertMaker:
        """Alerts with their explanation."""
        return AlertMaker(TemplateAlertExplainer(), self.world.runtime)

    def sender(self) -> RegistrySender:
        """Sends one request and applies its answer."""
        applier = ResultApplier(self.alert_maker(), self.world.runtime)
        return RegistrySender(self.world.units, self.registry, applier, self.world.runtime)

    def verify_certificate(self) -> VerifyCertificate:
        """Fetches, verifies and stores one certificate."""
        return VerifyCertificate(
            self.world.units, self.registry, self.signatures, self.member_id, self.world.runtime
        )

    def verify_checkpoint(self) -> VerifyCheckpoint:
        """Verifies one checkpoint against the ledger headers."""
        return VerifyCheckpoint(
            self.world.units, EventHeaderReader(self.registry), self.signatures, self.world.runtime
        )

    def outbox_sync(self) -> SyncRegistryOutbox:
        """The registry outbox sync."""
        handlers = OutboxHandlers(
            AlertMessages(self.alert_maker()), CaseUpdateMessages(self.world.log), self.verify_checkpoint()
        )
        source = OutboxSource(self.registry, self.outbox_lock)
        timing = OutboxTiming(30.0, timedelta(seconds=self.blind_after_seconds))
        return SyncRegistryOutbox(self.world.units, source, handlers, self.world.runtime, timing)

    def stages(self) -> tuple[CleanTape, RunLocalChecks, TokenizeLoans, SubmitStage, CollectCertificates]:
        """The five stages in order."""
        units, runtime = self.world.units, self.world.runtime
        sources = OutsideRecordsClients(self.outside, self.payments, self.outside)
        requests = TapeRequests(
            units, self.sender(), runtime, SubmitSettings(self.member_id, self.batch_items)
        )
        by_kind: dict[TapeKind, SubmissionUseCase] = {
            TapeKind.BACK_BOOK: SubmitPledge(requests),
            TapeKind.PLEDGE: SubmitPledge(requests),
            TapeKind.RELEASE: ReleaseLoans(requests),
        }
        holders = KeyHolders(self.holders[0], self.holders[1])
        return (
            CleanTape(units, runtime),
            RunLocalChecks(units, sources, LocalCheckRules(CheckWindows()), runtime, self.chunk_loans),
            TokenizeLoans(units, holders, self.blinder, runtime, self.chunk_loans),
            SubmitStage(units, self.halt_rule(), by_kind),
            CollectCertificates(units, self.verify_certificate(), self.outbox_sync(), runtime),
        )

    def advance(self) -> AdvanceSubmission:
        """Stage dispatch with the failure handling of SPEC 13.7."""
        return AdvanceSubmission(self.world.units, self.stages(), self.world.runtime, self.max_attempts)

    def worker(self) -> RunPipelineWorker:
        """The worker with the outbox sync as its periodic task."""
        settings = WorkerSettings(0.0, (self.outbox_sync(),), halt=self.halt_rule())
        return RunPipelineWorker(self.world.units, self.advance(), self.locks, self.world.runtime, settings)

    # -- actions -------------------------------------------------------------------------------

    def requester(self, actor: Actor | None = None) -> Requester:
        """A signed in user (the credit officer by default)."""
        return Requester(actor or self.officer, "req-1")

    def clear(self, vins: list[str]) -> None:
        """Outside records under which every local check passes for these VINs."""
        for vin in vins:
            self.outside.titles[vin] = TitleRecord("NJ", "CLEAN", "Summit Auto Finance LLC", None)
            self.outside.payment_records[vin] = [
                PaymentRecord(uuid.uuid4(), AS_OF - timedelta(days=5), 100, "HARBOR")
            ]
            self.outside.contract_records[vin] = [
                ContractRecord(uuid.uuid4(), "SUMMIT AUTO FINANCE LLC", AMOUNT_CENTS, ORIGINATION)
            ]

    def upload(
        self, loan_vins: list[str], kind: TapeKind = TapeKind.PLEDGE, as_of: date = AS_OF
    ) -> TapeSubmission:
        """Upload a Summit tape of these VINs, with clean outside records for each."""
        self.clear(loan_vins)
        data = summit_tape(
            [summit_row(vin=vin, loan_number=f"SAF-{n:07d}-01") for n, vin in enumerate(loan_vins)]
        )
        command = IngestCommand(self.world.facility.facility_id, kind, as_of, "t.csv", data, self.officer)
        return IngestTape(self.world.units, TapeFileReader(), AccessPolicy(), self.world.runtime).execute(
            command
        )

    def run(self, submission: TapeSubmission, until: TapeStatus = TapeStatus.COMPLETED) -> list[StageRun]:
        """Advance one submission stage by stage until `until`, a failure, a retry or nothing to do."""
        advance, runs = self.advance(), []
        while True:
            run = advance.advance(submission.submission_id)
            runs.append(run)
            if run.status is until or run.status is TapeStatus.FAILED or run.status is run.started_at_status:
                return runs

    def pledge(self, loan_vins: list[str]) -> TapeSubmission:
        """Upload and run a pledge tape to its end."""
        submission = self.upload(loan_vins)
        self.run(submission)
        return submission

    def tape(self, submission: TapeSubmission) -> TapeSubmission:
        """The submission as stored now."""
        return self.world.store.state.tapes[submission.submission_id]

    def loans(self) -> dict[str, Loan]:
        """Every loan by VIN."""
        return {loan.terms.vin.value: loan for loan in self.world.store.state.loans.values()}

    def alerts(self) -> list[Alert]:
        """Every alert."""
        return list(self.world.store.state.alerts.values())

    def retry(self, submission: TapeSubmission, actor: Actor | None = None) -> TapeSubmission:
        """`POST /v1/tapes/{id}/retry` as the credit officer (or `actor`)."""
        retry = RetryTape(self.world.units, self.authoriser(), self.world.runtime)
        return retry.execute(RetryCommand(submission.submission_id, self.requester(actor)))

    def release(self, loan_ids: list[uuid.UUID], as_of: date = AS_OF) -> TapeSubmission:
        """`POST /v1/releases` as the credit officer."""
        release = CreateReleaseInstruction(self.world.units, self.authoriser(), self.world.runtime)
        command = ReleaseCommand(self.world.facility.facility_id, as_of, tuple(loan_ids), self.requester())
        return release.execute(command)

    def review(
        self,
        alert_id: uuid.UUID,
        action: ReviewAction,
        position: BankPosition | None = None,
        actor: Actor | None = None,
    ) -> Alert:
        """`POST /v1/alerts/{id}/review`."""
        parts = ReviewParts(self.registry, self.authoriser(), self.outbox_sync(), self.halt_rule())
        review = ReviewAlert(self.world.units, parts, self.world.runtime)
        return review.execute(ReviewCommand(alert_id, action, self.requester(actor), position))

    def resubmit(self, loan_id: uuid.UUID) -> Loan:
        """`POST /v1/loans/{id}/resubmit` as the credit officer."""
        parts = ResubmitParts(
            self.sender(), self.verify_certificate(), self.authoriser(), self.member_id, self.halt_rule()
        )
        return ResubmitLoan(self.world.units, parts, self.world.runtime).execute(
            ResubmitCommand(loan_id, self.requester())
        )

    def report(self) -> IntegrityReport:
        """`GET /v1/integrity`."""
        blind_after = timedelta(seconds=self.blind_after_seconds)
        return GetIntegrityReport(self.world.units, self.controls, self.world.runtime, blind_after).execute()

    def verify(self) -> IntegrityReport:
        """`onepledge-bank integrity verify`."""
        blind_after = timedelta(seconds=self.blind_after_seconds)
        sources = VerificationSources(
            EventHeaderReader(self.registry), self.signatures, self.controls, blind_after
        )
        return RunDeepVerification(self.world.units, sources, self.world.runtime).execute(
            Requester.command_line()
        )

    def startup(self) -> RecordStartupConfiguration:
        """The startup audit of the halt rule."""
        return RecordStartupConfiguration(self.world.units, self.halt_enabled, self.world.runtime)
