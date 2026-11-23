"""`SyncRegistryOutbox`: pull and process this bank's registry messages (SPEC 14.1, SPEC_DECISIONS 101).

Runs from the worker every `OUTBOX_POLL_SECONDS`, at the end of the certify stage, before a
submission when the bank is blind, and on request (`onepledge-bank registry sync`). It holds
the advisory lock `onepledge:outbox`, so one sync runs per bank. Pages of 100 elements are
read in position order:

- A page that is not one bounded JSON object with a `messages` array and a boolean
  `has_more` cannot be acted on: the sync ends incomplete and nothing is acknowledged.
- An element without an object or a canonical id is quarantined once by its hash and the sync
  is incomplete; an element with a usable id is applied, ignored (an unknown type) or
  quarantined, given its row in `registry_outbox_messages` in the same transaction, then
  acknowledged (`outbox_outcomes.py`). A message the ledger shows as handled is a redelivery:
  only acknowledged, never handled or audited again.
- A deferred CHECKPOINT holds back later CHECKPOINTs only; alerts and case updates go on.
- An acknowledgement that fails for a passing reason is tried on the next sync; one the
  registry refuses is audited once (`REGISTRY_ACK_REFUSED`). Either leaves the sync incomplete.

A sync is complete when it reached `has_more: false`, every element had a usable id, every
message was handled and every acknowledgement taken. `registry_outbox_state` records the last
complete sync; a bank whose last one is missing or older than `OUTBOX_BLIND_HALT_SECONDS` is
blind, the fourth condition of the halt rule. Becoming blind and recovering are each audited once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.outbox_handlers import AlertMessages, CaseUpdateMessages
from onepledge_bank_agent.application.outbox_outcomes import (
    STORE_UNAVAILABLE,
    Poison,
    PoisonRecorder,
    deferral_reason,
    ledger_entry,
)
from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.registry_audit import ack_refused, blind_changed, message_ignored
from onepledge_bank_agent.application.registry_ports import OutboxLock, RegistryClient
from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.runtime import Runtime, StopRequestedError
from onepledge_bank_agent.application.verify_checkpoint import VerifyCheckpoint
from onepledge_bank_agent.domain.audit import AuditDraft
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, AuditOutcome
from onepledge_bank_agent.domain.errors import PipelineStageError
from onepledge_bank_agent.domain.integrity import render_time
from onepledge_bank_agent.domain.outbox import AckStatus, BlindChange, OutboxOutcome, OutboxState
from onepledge_bank_agent.domain.registry_records import (
    AlertNotice,
    CaseNotice,
    OutboxElement,
    OutboxMessage,
    QuarantinedPayload,
    SignedCheckpoint,
    UnacknowledgeableElement,
)
from onepledge_bank_agent.domain.store_errors import StoreUnavailableError

PAGE_SIZE = 100
CHECKPOINT = "CHECKPOINT"
UNACKNOWLEDGEABLE = "UNACKNOWLEDGEABLE"
CHECKPOINT_DEFERRED = "CHECKPOINT_DEFERRED"
NO_PROGRESS = "NO_PROGRESS"


@dataclass
class SyncResult:
    """What one sync did (`SyncResult` of `bank_agent.yaml`), and whether it was complete."""

    messages_processed: int = 0
    alerts_created: int = 0
    case_updates_applied: int = 0
    checkpoints_verified: int = 0
    checkpoints_failed: int = 0
    skipped: bool = field(default=False, compare=False)
    complete: bool = field(default=True, compare=False)
    reason: str | None = field(default=None, compare=False)
    failure: PipelineStageError | None = field(default=None, compare=False)
    """The dependency failure that ended the reading of pages, if any."""

    def counts(self) -> dict[str, int]:
        """The five counts."""
        return {
            "messages_processed": self.messages_processed,
            "alerts_created": self.alerts_created,
            "case_updates_applied": self.case_updates_applied,
            "checkpoints_verified": self.checkpoints_verified,
            "checkpoints_failed": self.checkpoints_failed,
        }

    def incomplete(self, reason: str) -> None:
        """The sync did not take in everything; the first reason is kept."""
        if self.complete:
            self.complete, self.reason = False, reason


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


@dataclass(frozen=True)
class OutboxTiming:
    """`OUTBOX_POLL_SECONDS` and `OUTBOX_BLIND_HALT_SECONDS`."""

    interval_seconds: float
    blind_after: timedelta


@dataclass
class _Pass:
    """One sync's running state: the result, and whether a checkpoint is waiting."""

    result: SyncResult
    checkpoint_waiting: bool = False
    stop: bool = False


class SyncRegistryOutbox:
    """Processes every pending outbox element of this bank."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        source: OutboxSource,
        handlers: OutboxHandlers,
        runtime: Runtime,
        timing: OutboxTiming,
    ) -> None:
        self._units = units
        self._registry = source.registry
        self._lock = source.lock
        self._handlers = handlers
        self._faults = runtime.faults
        self._stop = runtime.stop
        self._clock = runtime.clock
        self._log = runtime.log
        self._timing = timing
        self._poison = PoisonRecorder(units, runtime)

    @property
    def interval_seconds(self) -> float:
        """`OUTBOX_POLL_SECONDS`: how often the worker runs the sync."""
        return self._timing.interval_seconds

    def run(self, wait: bool = False) -> SyncResult:
        """The periodic, end of stage and catch-up sync; its result says whether it was complete. With
        `wait` a sync running elsewhere is waited for instead of skipped."""
        return self.sync(wait)

    def blind(self) -> bool:
        """The bank cannot see the registry's checkpoints now (SPEC_DECISIONS 101)."""
        with self._units() as unit:
            state = unit.outbox_state.get()
        return state.blind(self._clock.now(), self._timing.blind_after)

    def requested(self, requester: Requester) -> SyncResult:
        """A sync a user (or the command line) asked for; audited with its counts."""
        result = self.sync(wait=True)
        with self._units() as unit:
            detail = {**result.counts(), "complete": result.complete, **requester.detail()}
            unit.record(
                AuditDraft(
                    AuditAction.REGISTRY_SYNC_REQUESTED,
                    requester.actor,
                    AuditObjectType.REGISTRY,
                    None,
                    AuditOutcome.SUCCESS if result.complete else AuditOutcome.FAILURE,
                    detail,
                    requester.request_id,
                )
            )
            unit.commit()
        return result

    def sync(self, wait: bool = False) -> SyncResult:
        """Read pages until the registry has no more, recording whether the sync was complete."""
        current = _Pass(SyncResult())
        with self._lock.hold(wait) as granted:
            if not granted:
                self._log.info(LogEvent.OUTBOX_SYNC_SKIPPED)
                return SyncResult(skipped=True, complete=False, reason="SYNC_RUNNING")
            more = True
            try:
                while more and not current.stop:
                    more = self._page(current)
            except StoreUnavailableError:
                current.result.incomplete(STORE_UNAVAILABLE)
            if current.stop or self._stop.is_set():
                current.result.incomplete("STOPPED")
            try:
                self._record_state(current.result)
            except StoreUnavailableError:
                current.result.incomplete(STORE_UNAVAILABLE)  # the state is written by the next sync
        if current.result.messages_processed:
            self._log.info(LogEvent.OUTBOX_SYNCED, count=current.result.messages_processed)
        return current.result

    def _page(self, current: _Pass) -> bool:
        """One page; True when another page should be read."""
        try:
            page = self._registry.outbox(PAGE_SIZE)
        except PipelineStageError as error:
            current.result.incomplete(error.reason.value)
            current.result.failure = error
            self._log.warning(
                LogEvent.OUTBOX_SYNC_INTERRUPTED,
                failure_code=error.failure_code.value,
                **error.failure_facts(),
            )
            return False
        before = current.result.messages_processed
        for element in page.elements:
            self._element(element, current)
            if current.stop or self._stop.is_set():
                current.stop = True
                break
        progressed = current.result.messages_processed > before
        if page.has_more and not progressed:
            current.result.incomplete(NO_PROGRESS)
        return page.has_more and progressed

    def _element(self, element: OutboxElement, current: _Pass) -> None:
        if isinstance(element, UnacknowledgeableElement):
            self._poison.unacknowledgeable(element)
            current.result.incomplete(UNACKNOWLEDGEABLE)
            return
        with self._units() as unit:
            seen = unit.outbox_ledger.get(element.message_id)
        if seen is not None and seen.handled:  # a redelivery: acknowledged, never handled twice
            self._acknowledge(element, current)
            return
        if element.message_type == CHECKPOINT and current.checkpoint_waiting:
            current.result.incomplete(CHECKPOINT_DEFERRED)  # checkpoints are verified in order
            return
        if self._settle(element, current):
            self._faults.reached(FaultPoint.BETWEEN_OUTBOX_COMMIT_AND_ACK)
            self._acknowledge(element, current)

    def _settle(self, message: OutboxMessage, current: _Pass) -> bool:
        """Apply, ignore or quarantine the message; False when it is deferred."""
        payload = message.payload
        if isinstance(payload, QuarantinedPayload):
            self._poison.quarantine(message, Poison(payload.reason))
            return True
        try:
            self._apply(message, current.result)
        except StopRequestedError:
            raise
        except Exception as error:  # SPEC_DECISIONS 101: poison is defined by exclusion
            reason = deferral_reason(error)
            if reason is None:
                self._poison.quarantine(message, self._poison.failed(message, error))
                return True
            self._poison.defer(message, reason)
            current.result.incomplete(reason)
            if isinstance(error, StoreUnavailableError):
                current.stop = True  # the store itself is gone: nothing else can be handled now
            elif message.message_type == CHECKPOINT:
                current.checkpoint_waiting = True  # later checkpoints wait; other kinds go on
            return False
        return True

    def _apply(self, message: OutboxMessage, result: SyncResult) -> None:
        payload = message.payload
        if isinstance(payload, SignedCheckpoint):
            self._checkpoint(message, payload, result)
            return
        with self._units() as unit:
            if isinstance(payload, AlertNotice):
                result.alerts_created += int(self._handlers.alerts.handle(unit, payload))
                outcome = OutboxOutcome.APPLIED
            elif isinstance(payload, CaseNotice):
                result.case_updates_applied += self._handlers.case_updates.handle(unit, payload)
                outcome = OutboxOutcome.APPLIED
            else:
                unit.record(message_ignored(message.message_id, message.message_type))
                self._log.warning(LogEvent.REGISTRY_MESSAGE_IGNORED)
                outcome = OutboxOutcome.IGNORED
            self._handled(unit, message, outcome)
            self._faults.reached(FaultPoint.OUTBOX_HANDLER_BEFORE_COMMIT)
            unit.commit()

    def _checkpoint(self, message: OutboxMessage, signed: SignedCheckpoint, result: SyncResult) -> None:
        assessment = self._handlers.checkpoints.assess(signed)
        with self._units() as unit:
            stored = None
            if not assessment.duplicate and assessment.verdict is not None:
                stored = self._handlers.checkpoints.record(unit, signed, assessment.verdict)
            self._handled(unit, message, OutboxOutcome.APPLIED)
            self._faults.reached(FaultPoint.OUTBOX_HANDLER_BEFORE_COMMIT)
            unit.commit()
        if stored is None:
            return
        if stored.verified:
            result.checkpoints_verified += 1
        else:
            result.checkpoints_failed += 1

    def _handled(self, unit: UnitOfWork, message: OutboxMessage, outcome: OutboxOutcome) -> None:
        now = self._clock.now()
        entry = ledger_entry(unit, message, now)
        if not entry.handled:
            unit.outbox_ledger.save(entry.handled_as(outcome, now))

    def _acknowledge(self, message: OutboxMessage, current: _Pass) -> None:
        """Acknowledge; a passing failure is tried next time, a refusal is audited once."""
        with self._units() as unit:
            entry = unit.outbox_ledger.get(message.message_id)
        status, reason = AckStatus.ACKED, None
        try:
            self._registry.acknowledge(message.message_id)
        except PipelineStageError as error:
            status = AckStatus.PENDING if error.transient else AckStatus.REFUSED
            reason = error.reason.value
            current.result.incomplete(f"ACK_{status.value}")
            self._log.warning(
                LogEvent.OUTBOX_ACK_FAILED, failure_code=error.failure_code.value, **error.failure_facts()
            )
        now = self._clock.now()
        with self._units() as unit:
            if entry is not None and entry.ack_status is not status:
                unit.outbox_ledger.save(entry.acknowledged(status, now, reason))
                if status is AckStatus.REFUSED:
                    unit.record(ack_refused(message.message_id, message.message_type, reason or ""))
            unit.commit()
        if status is AckStatus.ACKED:
            current.result.messages_processed += 1

    def _record_state(self, result: SyncResult) -> None:
        """Store the completeness of this sync; audit becoming blind and recovering once each."""
        now = self._clock.now()
        with self._units() as unit:
            state: OutboxState = unit.outbox_state.get()
            after, change = state.after_sync(now, result.complete, result.reason, self._timing.blind_after)
            unit.outbox_state.save(after)
            if change is BlindChange.BECAME_BLIND:
                since = render_time(after.blind_since) if after.blind_since is not None else None
                unit.record(blind_changed(True, after.last_reason, since))
                self._log.warning(LogEvent.OUTBOX_BLIND, reason=after.last_reason)
            elif change is BlindChange.RECOVERED:
                unit.record(blind_changed(False, None, None))
                self._log.info(LogEvent.OUTBOX_RECOVERED)
            unit.commit()
