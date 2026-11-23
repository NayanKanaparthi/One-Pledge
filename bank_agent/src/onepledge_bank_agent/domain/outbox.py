"""The registry outbox as the bank records it (SPEC_DECISIONS 101).

- `LedgerEntry`, a row of `registry_outbox_messages`: one per message with a usable id, written
  in the handler's transaction. It says how the message was handled (applied, ignored,
  quarantined, or applied late after its alert arrived) and whether the registry took the
  acknowledgement, so a redelivered message is never handled or audited twice. `outcome` is set
  once, except that a quarantined unmatched case update may later become `APPLIED_LATE`.
- `OutboxState`, the single row of `registry_outbox_state`: when the last complete sync ended,
  since when syncs have been incomplete and why, and since when the bank is blind.
- `blind`: a bank whose last complete sync is missing or older than `OUTBOX_BLIND_HALT_SECONDS`
  cannot see the registry's checkpoints. It is the fourth condition of the halt rule and the
  only one that clears by itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from onepledge_bank_agent.domain.errors import IllegalTransitionError


class OutboxOutcome(StrEnum):
    """How a message was handled."""

    APPLIED = "APPLIED"
    IGNORED = "IGNORED"
    QUARANTINED = "QUARANTINED"
    APPLIED_LATE = "APPLIED_LATE"


class AckStatus(StrEnum):
    """Whether the registry took the acknowledgement."""

    PENDING = "PENDING"
    ACKED = "ACKED"
    REFUSED = "REFUSED"


class BlindChange(StrEnum):
    """A change of the blind state that is audited once."""

    BECAME_BLIND = "BECAME_BLIND"
    RECOVERED = "RECOVERED"


@dataclass(frozen=True)
class LedgerEntry:
    """One outbox message the bank has seen."""

    message_id: uuid.UUID
    message_type: str
    first_seen_at: datetime
    handled_at: datetime | None
    outcome: OutboxOutcome | None
    ack_status: AckStatus
    last_reason: str | None
    updated_at: datetime

    @classmethod
    def first_seen(cls, message_id: uuid.UUID, message_type: str, at: datetime) -> LedgerEntry:
        """A message seen for the first time, not yet handled."""
        return cls(message_id, message_type, at, None, None, AckStatus.PENDING, None, at)

    @property
    def handled(self) -> bool:
        """The message was applied, ignored or quarantined; a redelivery is only acknowledged."""
        return self.outcome is not None

    def handled_as(self, outcome: OutboxOutcome, at: datetime) -> LedgerEntry:
        """The entry once handled; the outcome is set once, except QUARANTINED to APPLIED_LATE."""
        late = self.outcome is OutboxOutcome.QUARANTINED and outcome is OutboxOutcome.APPLIED_LATE
        if self.outcome is not None and not late:
            raise IllegalTransitionError("outbox_outcome", self.outcome.value, outcome.value)
        return replace(self, handled_at=self.handled_at or at, outcome=outcome, updated_at=at)

    def deferred(self, reason: str, at: datetime) -> LedgerEntry:
        """The entry after a handling that will be tried again."""
        return replace(self, last_reason=reason, updated_at=at)

    def acknowledged(self, status: AckStatus, at: datetime, reason: str | None = None) -> LedgerEntry:
        """The entry after an acknowledgement was answered."""
        return replace(self, ack_status=status, last_reason=reason or self.last_reason, updated_at=at)


@dataclass(frozen=True)
class OutboxState:
    """The single row of `registry_outbox_state`."""

    last_complete_sync_at: datetime | None = None
    incomplete_since: datetime | None = None
    last_reason: str | None = None
    blind_since: datetime | None = None

    def blind(self, now: datetime, limit: timedelta) -> bool:
        """No complete sync yet, or the last one is older than the limit."""
        return self.last_complete_sync_at is None or now - self.last_complete_sync_at > limit

    def after_sync(
        self, now: datetime, complete: bool, reason: str | None, limit: timedelta
    ) -> tuple[OutboxState, BlindChange | None]:
        """The state after one sync, and the blind change to audit, if any."""
        if complete:
            state = OutboxState(now, None, None, self.blind_since)
        else:
            state = OutboxState(
                self.last_complete_sync_at, self.incomplete_since or now, reason, self.blind_since
            )
        blind = state.blind(now, limit)
        if blind and state.blind_since is None:
            since = now if state.last_complete_sync_at is None else state.last_complete_sync_at + limit
            return replace(state, blind_since=min(since, now)), BlindChange.BECAME_BLIND
        if not blind and state.blind_since is not None:
            return replace(state, blind_since=None), BlindChange.RECOVERED
        return state, None
