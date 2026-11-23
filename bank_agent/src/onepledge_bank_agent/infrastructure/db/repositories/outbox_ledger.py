"""`SqlOutboxLedgerRepository` and `SqlOutboxStateRepository` (SPEC_DECISIONS 101).

`registry_outbox_messages` holds one row per message with a usable id, written in the
transaction that handled it; its guard (migration 0013) keeps the id, the type and the first
sight, sets the outcome once (a quarantined case update may become APPLIED_LATE) and refuses
every DELETE. `registry_outbox_state` is one row, its key always true.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from onepledge_bank_agent.domain.outbox import AckStatus, LedgerEntry, OutboxOutcome, OutboxState
from onepledge_bank_agent.infrastructure.db.models import OutboxMessageRow, OutboxStateRow
from onepledge_bank_agent.infrastructure.db.readable import read_rows

TABLE = "registry_outbox_messages"


def _entry(row: OutboxMessageRow) -> LedgerEntry:
    return LedgerEntry(
        row.message_id,
        row.message_type,
        row.first_seen_at,
        row.handled_at,
        OutboxOutcome(row.outcome) if row.outcome is not None else None,
        AckStatus(row.ack_status),
        row.last_reason,
        row.updated_at,
    )


class SqlOutboxLedgerRepository:
    """Every outbox message seen."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, message_id: uuid.UUID) -> LedgerEntry | None:
        """The message's row, if seen."""
        statement = select(OutboxMessageRow).where(OutboxMessageRow.message_id == message_id)
        found = read_rows(
            self._session, statement, OutboxMessageRow.message_id, _entry, OutboxMessageRow.__tablename__
        )
        return found[0] if found else None

    def save(self, entry: LedgerEntry) -> None:
        """Insert the row, or update what may change."""
        values = {
            "message_id": entry.message_id,
            "message_type": entry.message_type,
            "first_seen_at": entry.first_seen_at,
            "handled_at": entry.handled_at,
            "outcome": entry.outcome.value if entry.outcome is not None else None,
            "ack_status": entry.ack_status.value,
            "last_reason": entry.last_reason,
            "updated_at": entry.updated_at,
        }
        statement = insert(OutboxMessageRow).values(**values)
        mutable = ("handled_at", "outcome", "ack_status", "last_reason", "updated_at")
        self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[OutboxMessageRow.message_id],
                set_={name: getattr(statement.excluded, name) for name in mutable},
            )
        )

    def applied_late(self) -> set[uuid.UUID]:
        """The messages whose quarantined case update was applied once their alert arrived."""
        statement = select(OutboxMessageRow.message_id).where(
            OutboxMessageRow.outcome == OutboxOutcome.APPLIED_LATE.value
        )
        return set(self._session.execute(statement).scalars())


class SqlOutboxStateRepository:
    """The single row of outbox completeness."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self) -> OutboxState:
        """The stored state, or the initial one (never complete)."""
        row = self._session.get(OutboxStateRow, True)
        if row is None:
            return OutboxState()
        return OutboxState(row.last_complete_sync_at, row.incomplete_since, row.last_reason, row.blind_since)

    def save(self, state: OutboxState) -> None:
        """Insert or replace the row."""
        values = {
            "last_complete_sync_at": state.last_complete_sync_at,
            "incomplete_since": state.incomplete_since,
            "last_reason": state.last_reason,
            "blind_since": state.blind_since,
        }
        statement = insert(OutboxStateRow).values(singleton=True, **values)
        self._session.execute(
            statement.on_conflict_do_update(index_elements=[OutboxStateRow.singleton], set_=values)
        )
