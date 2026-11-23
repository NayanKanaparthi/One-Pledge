"""`SqlQuarantineRepository`: `registry_messages_quarantined` (insert-only, SPEC_DECISIONS 57, 101).

One row per message, by its message id; an element without a usable id is kept once by its
payload hash. `payload_withheld` says the payload is kept as its hash alone (oversized, or a
value the bank cannot store); a payload the registry sent as JSON `null` is kept as such.
"""

from __future__ import annotations

import uuid

from sqlalchemy import exists, func, null, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from onepledge_bank_agent.domain.enums import QuarantineReason
from onepledge_bank_agent.domain.registry_records import QuarantinedMessage, QuarantinedPayload
from onepledge_bank_agent.infrastructure.db.models import QuarantinedMessageRow
from onepledge_bank_agent.infrastructure.db.readable import read_rows

CHECKPOINT_TYPE = "CHECKPOINT"
CASE_UPDATE_TYPE = "CASE_UPDATE"
TABLE = "registry_messages_quarantined"


def _message(row: QuarantinedMessageRow) -> QuarantinedMessage:
    payload = None if row.payload_withheld else row.payload
    held = QuarantinedPayload(QuarantineReason(row.reason), payload, row.payload_sha256, row.payload_withheld)
    return QuarantinedMessage(
        row.quarantine_id, row.message_id, row.position, row.message_type, held, row.received_at
    )


class SqlQuarantineRepository:
    """Keeps each quarantined message once."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, message: QuarantinedMessage) -> bool:
        """Insert one row; False when it is already held (a delivery repeated)."""
        quarantined = message.quarantined
        values = {
            "quarantine_id": message.quarantine_id,
            "message_id": message.message_id,
            "position": message.position,
            "message_type": message.message_type,
            "payload": null() if quarantined.withheld else quarantined.payload,  # SQL NULL, not JSON null
            "payload_withheld": quarantined.withheld,
            "payload_sha256": quarantined.payload_sha256,
            "reason": quarantined.reason.value,
            "received_at": message.received_at,
        }
        statement = insert(QuarantinedMessageRow).values(**values).on_conflict_do_nothing()
        return (
            self._session.execute(statement.returning(QuarantinedMessageRow.quarantine_id)).first()
            is not None
        )

    def count(self) -> int:
        """Quarantined messages held."""
        return int(
            self._session.execute(select(func.count()).select_from(QuarantinedMessageRow)).scalar_one()
        )

    def any_checkpoint(self) -> bool:
        """A quarantined CHECKPOINT message exists."""
        statement = select(exists().where(QuarantinedMessageRow.message_type == CHECKPOINT_TYPE))
        return bool(self._session.execute(statement).scalar_one())

    def all(self) -> list[QuarantinedMessage]:
        """Every quarantined message, oldest first; a row that cannot be read back is named."""
        statement = select(QuarantinedMessageRow).order_by(
            QuarantinedMessageRow.received_at, QuarantinedMessageRow.quarantine_id
        )
        return read_rows(self._session, statement, QuarantinedMessageRow.quarantine_id, _message, TABLE)

    def unmatched_case_updates(self, case_id: uuid.UUID, asset_token: str) -> list[QuarantinedMessage]:
        """The CASE_UPDATE messages quarantined UNMATCHED for this case and asset code, newest first."""
        row = QuarantinedMessageRow
        statement = (
            select(row)
            .where(
                row.reason == QuarantineReason.UNMATCHED.value,
                row.message_type == CASE_UPDATE_TYPE,
                row.payload["case_id"].astext == str(case_id),
                row.payload["asset_token"].astext == asset_token,
            )
            .order_by(row.received_at.desc(), row.quarantine_id.desc())
        )
        return read_rows(self._session, statement, row.quarantine_id, _message, TABLE)
