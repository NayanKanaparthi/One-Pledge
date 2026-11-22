"""`SqlQuarantineRepository`: `registry_messages_quarantined` (insert-only, SPEC_DECISIONS 57)."""

from __future__ import annotations

from sqlalchemy import exists, func, null, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from onepledge_bank_agent.domain.enums import QuarantineReason
from onepledge_bank_agent.domain.registry_records import QuarantinedMessage
from onepledge_bank_agent.infrastructure.db.models import QuarantinedMessageRow

CHECKPOINT_TYPE = "CHECKPOINT"


class SqlQuarantineRepository:
    """Keeps each quarantined message once, by its message id."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, message: QuarantinedMessage) -> bool:
        """Insert one row; False when the message is already quarantined (a delivery repeated)."""
        quarantined = message.quarantined
        oversized = quarantined.reason is QuarantineReason.OVERSIZED
        statement = (
            insert(QuarantinedMessageRow)
            .values(
                quarantine_id=message.quarantine_id,
                message_id=message.message_id,
                position=message.position,
                message_type=message.message_type,
                payload=null() if oversized else quarantined.payload,
                payload_sha256=quarantined.payload_sha256,
                reason=quarantined.reason.value,
                received_at=message.received_at,
            )
            .on_conflict_do_nothing(constraint="uq_registry_messages_quarantined_message_id")
            .returning(QuarantinedMessageRow.quarantine_id)
        )
        return self._session.execute(statement).first() is not None

    def count(self) -> int:
        """Quarantined messages held."""
        return int(
            self._session.execute(select(func.count()).select_from(QuarantinedMessageRow)).scalar_one()
        )

    def any_checkpoint(self) -> bool:
        """A quarantined CHECKPOINT message exists."""
        statement = select(exists().where(QuarantinedMessageRow.message_type == CHECKPOINT_TYPE))
        return bool(self._session.execute(statement).scalar_one())
