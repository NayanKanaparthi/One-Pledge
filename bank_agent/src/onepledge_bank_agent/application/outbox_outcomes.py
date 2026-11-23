"""What happens to one registry outbox element (SPEC_DECISIONS 101).

- Applied: the handler committed, its ledger row in the same transaction.
- Deferred: only for a lost or timed out database connection, a serialisation failure or a
  deadlock (`StoreUnavailableError`), or a passing class of decision 49 while calling the
  registry. The message is not acknowledged and comes back; a deferral is never turned into
  poison. What protects the bank meanwhile is the blind rule, which clears by itself.
- Poison: everything else. A message that failed the contract or decision 100, contradicts
  stored state (`CONTRADICTS_STORED_STATE`), names no alert (`UNMATCHED`, kept to be applied
  late), was refused by the store (`REFUSED_BY_DATABASE`, with the SQLSTATE) or by a
  dependency, or raised anything the handler did not anticipate. It is quarantined, audited
  and given its ledger row in one transaction, then acknowledged.
- Unacknowledgeable: an element without an object or a canonical id is quarantined once by
  its hash and audited; it cannot be acknowledged, so the sync is incomplete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from onepledge_bank_agent.application.error_records import error_code_of
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.registry_audit import message_quarantined
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.enums import QuarantineReason
from onepledge_bank_agent.domain.errors import PipelineStageError
from onepledge_bank_agent.domain.outbox import LedgerEntry, OutboxOutcome
from onepledge_bank_agent.domain.registry_records import (
    MessageEvidence,
    OutboxMessage,
    QuarantinedMessage,
    QuarantinedPayload,
    UnacknowledgeableElement,
)
from onepledge_bank_agent.domain.store_errors import (
    ContradictsStoredStateError,
    StoreRefusedError,
    StoreUnavailableError,
    UnmatchedCaseUpdateError,
)

STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
UNREADABLE_TYPE = "UNREADABLE"


@dataclass(frozen=True)
class Poison:
    """Why a message is quarantined, and the SQLSTATE when the store refused it."""

    reason: QuarantineReason
    sqlstate: str | None = None


def deferral_reason(error: Exception) -> str | None:
    """The reason a failure only defers the message, or None when it is poison."""
    if isinstance(error, StoreUnavailableError):
        return STORE_UNAVAILABLE
    if isinstance(error, PipelineStageError) and error.transient:
        return error.failure_code.value
    return None


def poison_of(error: Exception) -> Poison:
    """The quarantine reason of a failure that is not a deferral."""
    if isinstance(error, StoreRefusedError):
        return Poison(QuarantineReason.REFUSED_BY_DATABASE, error.sqlstate)
    if isinstance(error, ContradictsStoredStateError):
        return Poison(QuarantineReason.CONTRADICTS_STORED_STATE)
    if isinstance(error, UnmatchedCaseUpdateError):
        return Poison(QuarantineReason.UNMATCHED)
    if isinstance(error, PipelineStageError) and error.dependency is not None:
        return Poison(QuarantineReason.REJECTED_BY_DEPENDENCY)
    return Poison(QuarantineReason.UNEXPECTED_ERROR)


def ledger_entry(unit: UnitOfWork, message: OutboxMessage, at: datetime) -> LedgerEntry:
    """The message's ledger row as stored, or a new one first seen at `at`."""
    found = unit.outbox_ledger.get(message.message_id)
    return (
        found if found is not None else LedgerEntry.first_seen(message.message_id, message.message_type, at)
    )


class PoisonRecorder:
    """Quarantines poison and unacknowledgeable elements and records deferrals, one transaction each."""

    def __init__(self, units: UnitOfWorkFactory, runtime: Runtime) -> None:
        self._units = units
        self._clock = runtime.clock
        self._ids = runtime.ids
        self._log = runtime.log

    def defer(self, message: OutboxMessage, reason: str) -> None:
        """Record why the message waits; it is handled again on the next sync."""
        self._log.warning(LogEvent.OUTBOX_MESSAGE_DEFERRED, kind=message.message_type, reason=reason)
        now = self._clock.now()
        with self._units() as unit:  # StoreUnavailableError ends the sync: the message comes back anyway
            unit.outbox_ledger.save(ledger_entry(unit, message, now).deferred(reason, now))
            unit.commit()

    def failed(self, message: OutboxMessage, error: Exception) -> Poison:
        """The poison a handler failure stands for; an unanticipated one is also an `error` record."""
        poison = poison_of(error)
        if poison.reason is QuarantineReason.UNEXPECTED_ERROR:
            self._log.failure(
                LogEvent.OUTBOX_MESSAGE_FAILED,
                error,
                error_code=error_code_of(error),
                kind=message.message_type,
            )
        return poison

    def quarantine(self, message: OutboxMessage, poison: Poison) -> bool:
        """Store and audit the message as poison with its ledger row; False when it was already held."""
        evidence = message.evidence
        held = QuarantinedPayload(poison.reason, evidence.payload, evidence.payload_sha256, evidence.withheld)
        row = QuarantinedMessage(
            self._ids.new_id(), message.message_id, message.position, message.message_type, held,
            self._clock.now(),
        )  # fmt: skip
        with self._units() as unit:
            stored = unit.evidence.quarantine.add(row)
            if stored:
                unit.record(message_quarantined(row, poison.sqlstate))
            entry = ledger_entry(unit, message, row.received_at)
            if not entry.handled:
                unit.outbox_ledger.save(entry.handled_as(OutboxOutcome.QUARANTINED, row.received_at))
            unit.commit()
        if stored:
            self._log.warning(
                LogEvent.REGISTRY_MESSAGE_QUARANTINED, kind=message.message_type, reason=poison.reason.value
            )
        return stored

    def unacknowledgeable(self, element: UnacknowledgeableElement) -> bool:
        """Keep an element without a usable id once, by its hash, and audit it."""
        evidence: MessageEvidence = element.evidence
        held = QuarantinedPayload(
            QuarantineReason.UNACKNOWLEDGEABLE, evidence.payload, evidence.payload_sha256, evidence.withheld
        )
        row = QuarantinedMessage(self._ids.new_id(), None, None, UNREADABLE_TYPE, held, self._clock.now())
        with self._units() as unit:
            stored = unit.evidence.quarantine.add(row)
            if stored:
                unit.record(message_quarantined(row))
            unit.commit()
        if stored:
            self._log.warning(
                LogEvent.REGISTRY_MESSAGE_QUARANTINED, kind=UNREADABLE_TYPE, reason=held.reason.value
            )
        return stored
