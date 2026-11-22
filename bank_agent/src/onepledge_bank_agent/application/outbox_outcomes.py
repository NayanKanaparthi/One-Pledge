"""The three outcomes of processing one registry outbox message (SPEC_DECISIONS 85).

- Applied: the handler committed.
- Deferred: the bank's store could not complete (`StoreUnavailableError`), or a passing
  class of decision 49 came back while calling the registry.
- Poison: everything else. A message that failed validation, contradicts stored state,
  was refused by the store or by a dependency, or raised anything the handler did not
  anticipate.

A deferred message is not acknowledged and stops the sync; its deferrals are counted in
`registry_outbox_attempts`, each in its own transaction, and the fifth makes it poison
(`REPEATED_FAILURE`). A poison message is stored in `registry_messages_quarantined` with
its reason and audited as `REGISTRY_MESSAGE_QUARANTINED` in a new transaction, then the
sync acknowledges it and goes on. The class is defined by exclusion, so no failure can
fall outside it.
"""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_bank_agent.application.error_records import error_code_of
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.application.registry_audit import message_quarantined
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.enums import QuarantineReason
from onepledge_bank_agent.domain.errors import PipelineStageError
from onepledge_bank_agent.domain.registry_records import (
    OutboxMessage,
    QuarantinedMessage,
    QuarantinedPayload,
)
from onepledge_bank_agent.domain.store_errors import (
    ContradictsStoredStateError,
    StoreRefusedError,
    StoreUnavailableError,
)

MAX_DEFERRALS = 5
STORE_UNAVAILABLE = "STORE_UNAVAILABLE"


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
    if isinstance(error, PipelineStageError) and error.dependency is not None:
        return Poison(QuarantineReason.REJECTED_BY_DEPENDENCY)
    return Poison(QuarantineReason.UNEXPECTED_ERROR)


class PoisonRecorder:
    """Counts deferrals and quarantines poison, each in its own transaction."""

    def __init__(self, units: UnitOfWorkFactory, runtime: Runtime) -> None:
        self._units = units
        self._clock = runtime.clock
        self._ids = runtime.ids
        self._log = runtime.log

    def defer(self, message: OutboxMessage, reason: str) -> bool:
        """Count one deferral; True when the message has now been deferred too often and is poison."""
        with self._units() as unit:
            attempts = unit.outbox_attempts.defer(message.message_id, reason, self._clock.now())
            unit.commit()
        self._log.warning(
            LogEvent.OUTBOX_MESSAGE_DEFERRED, kind=message.message_type, reason=reason, attempts=attempts
        )
        return attempts >= MAX_DEFERRALS

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
        """Store and audit the message as poison; False when it was already held (a redelivery)."""
        evidence = message.evidence
        held = QuarantinedPayload(poison.reason, evidence.payload, evidence.payload_sha256)
        row = QuarantinedMessage(
            self._ids.new_id(), message.message_id, message.position, message.message_type, held,
            self._clock.now(),
        )  # fmt: skip
        with self._units() as unit:
            stored = unit.evidence.quarantine.add(row)
            if stored:
                unit.record(message_quarantined(row, poison.sqlstate))
            unit.commit()
        if stored:
            self._log.warning(
                LogEvent.REGISTRY_MESSAGE_QUARANTINED, kind=message.message_type, reason=poison.reason.value
            )
        return stored
