"""`SyncRegistryOutbox`: pull and process this bank's registry messages (SPEC 14.1).

Runs from the worker every `OUTBOX_POLL_SECONDS`, at the end of the certify stage, on
request (`POST /v1/registry/sync`, `onepledge-bank registry sync`). It holds the advisory
lock `onepledge:outbox`, so one sync runs per bank. Messages are read 100 at a time in
position order; each is processed in one transaction, committed, then acknowledged. A
crash between commit and acknowledgement causes a redelivery that changes nothing. A
message type this bank does not know is acknowledged and audited. Every other message
has one of three outcomes (SPEC_DECISIONS 85, `outbox_outcomes.py`): applied, deferred
(the sync stops and the message comes back next time, at most five times), or poison
(quarantined, audited, acknowledged, and the sync goes on). Nothing a registry sends can
block the outbox or vanish. An outage ends the periodic sync quietly; an on-demand sync
reports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.outbox_handlers import AlertMessages, CaseUpdateMessages
from onepledge_bank_agent.application.outbox_outcomes import Poison, PoisonRecorder, deferral_reason
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.application.registry_audit import message_ignored
from onepledge_bank_agent.application.registry_ports import OutboxLock, RegistryClient
from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.runtime import Runtime, StopRequestedError
from onepledge_bank_agent.application.verify_checkpoint import VerifyCheckpoint
from onepledge_bank_agent.domain.audit import AuditDraft
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, AuditOutcome, QuarantineReason
from onepledge_bank_agent.domain.errors import PipelineStageError
from onepledge_bank_agent.domain.registry_records import (
    AlertNotice,
    CaseNotice,
    OutboxMessage,
    QuarantinedPayload,
    SignedCheckpoint,
)

PAGE_SIZE = 100


@dataclass
class SyncResult:
    """What one sync did (`SyncResult` of `bank_agent.yaml`)."""

    messages_processed: int = 0
    alerts_created: int = 0
    case_updates_applied: int = 0
    checkpoints_verified: int = 0
    checkpoints_failed: int = 0
    skipped: bool = field(default=False, compare=False)

    def counts(self) -> dict[str, int]:
        """The five counts."""
        return {
            "messages_processed": self.messages_processed,
            "alerts_created": self.alerts_created,
            "case_updates_applied": self.case_updates_applied,
            "checkpoints_verified": self.checkpoints_verified,
            "checkpoints_failed": self.checkpoints_failed,
        }


@dataclass(frozen=True)
class OutboxSource:
    """Where the messages come from, and the lock that lets one sync read them at a time."""

    registry: RegistryClient
    lock: OutboxLock


@dataclass(frozen=True)
class OutboxHandlers:
    """The message handlers and the checkpoint verifier."""

    alerts: AlertMessages
    case_updates: CaseUpdateMessages
    checkpoints: VerifyCheckpoint


class SyncRegistryOutbox:
    """Processes every pending outbox message of this bank."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        source: OutboxSource,
        handlers: OutboxHandlers,
        runtime: Runtime,
        interval_seconds: float,
    ) -> None:
        self._units = units
        self._registry = source.registry
        self._lock = source.lock
        self._handlers = handlers
        self._faults = runtime.faults
        self._stop = runtime.stop
        self._clock = runtime.clock
        self._ids = runtime.ids
        self._log = runtime.log
        self._interval = interval_seconds
        self._poison = PoisonRecorder(units, runtime)

    @property
    def interval_seconds(self) -> float:
        """`OUTBOX_POLL_SECONDS`: how often the worker runs the sync."""
        return self._interval

    def run(self) -> None:
        """The periodic and end of stage sync: a dependency failure ends it quietly, to run again later."""
        try:
            self.sync()
        except PipelineStageError as error:
            self._log.warning(
                LogEvent.OUTBOX_SYNC_INTERRUPTED,
                failure_code=error.failure_code.value,
                **error.failure_facts(),
            )

    def requested(self, requester: Requester) -> SyncResult:
        """A sync a user (or the command line) asked for; audited with its counts. Raises on an outage."""
        result = self.sync()
        with self._units() as unit:
            unit.record(
                AuditDraft(
                    AuditAction.REGISTRY_SYNC_REQUESTED,
                    requester.actor,
                    AuditObjectType.REGISTRY,
                    None,
                    AuditOutcome.SUCCESS,
                    {**result.counts(), **requester.detail()},
                    requester.request_id,
                )
            )
            unit.commit()
        return result

    def sync(self) -> SyncResult:
        """Process pages until none is waiting. Each message is applied, deferred or quarantined as
        poison (SPEC_DECISIONS 85); a deferral stops the sync by raising the failure that caused it."""
        result = SyncResult()
        with self._lock.hold() as granted:
            if not granted:
                self._log.info(LogEvent.OUTBOX_SYNC_SKIPPED)
                return SyncResult(skipped=True)
            more = True
            while more:
                page = self._registry.outbox(PAGE_SIZE)
                for message in page.messages:
                    self._settle(message, result)
                    self._faults.reached(FaultPoint.BETWEEN_OUTBOX_COMMIT_AND_ACK)
                    self._registry.acknowledge(message.message_id)
                    result.messages_processed += 1
                    if self._stop.is_set():  # SPEC_DECISIONS 51: stop after the acknowledged message
                        break
                more = page.has_more and bool(page.messages) and not self._stop.is_set()
        if result.messages_processed:
            self._log.info(LogEvent.OUTBOX_SYNCED, count=result.messages_processed)
        return result

    def _settle(self, message: OutboxMessage, result: SyncResult) -> None:
        """Apply the message, or quarantine it as poison; raise only when it is deferred."""
        payload = message.payload
        if isinstance(payload, QuarantinedPayload):
            self._poison.quarantine(message, Poison(payload.reason))
            return
        try:
            self._apply(message, result)
        except StopRequestedError:
            raise
        except Exception as error:  # SPEC_DECISIONS 85: the poison class is defined by exclusion
            reason = deferral_reason(error)
            if reason is not None and not self._poison.defer(message, reason):
                raise
            repeated = Poison(QuarantineReason.REPEATED_FAILURE)
            self._poison.quarantine(
                message, repeated if reason is not None else self._poison.failed(message, error)
            )

    def _apply(self, message: OutboxMessage, result: SyncResult) -> None:
        payload = message.payload
        if isinstance(payload, SignedCheckpoint):
            self._checkpoint(payload, result)
            return
        with self._units() as unit:
            if isinstance(payload, AlertNotice):
                result.alerts_created += int(self._handlers.alerts.handle(unit, payload))
            elif isinstance(payload, CaseNotice):
                result.case_updates_applied += self._handlers.case_updates.handle(unit, payload)
            else:
                unit.record(message_ignored(message.message_id, message.message_type))
                self._log.warning(LogEvent.REGISTRY_MESSAGE_IGNORED)
            self._faults.reached(FaultPoint.OUTBOX_HANDLER_BEFORE_COMMIT)
            unit.commit()

    def _checkpoint(self, signed: SignedCheckpoint, result: SyncResult) -> None:
        assessment = self._handlers.checkpoints.assess(signed)
        if assessment.duplicate or assessment.verdict is None:
            return
        with self._units() as unit:
            stored = self._handlers.checkpoints.record(unit, signed, assessment.verdict)
            self._faults.reached(FaultPoint.OUTBOX_HANDLER_BEFORE_COMMIT)
            unit.commit()
        if stored is None:
            return
        if stored.verified:
            result.checkpoints_verified += 1
        else:
            result.checkpoints_failed += 1
