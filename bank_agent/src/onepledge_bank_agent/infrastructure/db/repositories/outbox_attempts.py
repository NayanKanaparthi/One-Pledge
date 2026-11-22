"""`SqlOutboxAttemptRepository`: `registry_outbox_attempts` (SPEC_DECISIONS 85, rule 4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from onepledge_bank_agent.infrastructure.db.models import OutboxAttemptRow


class SqlOutboxAttemptRepository:
    """One row per deferred message; each deferral adds one."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def count(self, message_id: uuid.UUID) -> int:
        """The deferrals recorded for a message."""
        statement = select(OutboxAttemptRow.attempts).where(OutboxAttemptRow.message_id == message_id)
        found: int | None = self._session.execute(statement).scalar_one_or_none()
        return found or 0

    def defer(self, message_id: uuid.UUID, reason: str, at: datetime) -> int:
        """Insert the first deferral or count one more, in one statement."""
        statement = insert(OutboxAttemptRow).values(
            message_id=message_id, attempts=1, last_reason=reason, last_at=at
        )
        upsert = statement.on_conflict_do_update(
            index_elements=[OutboxAttemptRow.message_id],
            set_={
                "attempts": OutboxAttemptRow.attempts + 1,
                "last_reason": statement.excluded.last_reason,
                "last_at": statement.excluded.last_at,
            },
        ).returning(OutboxAttemptRow.attempts)
        attempts: int = self._session.execute(upsert).scalar_one()
        return attempts
