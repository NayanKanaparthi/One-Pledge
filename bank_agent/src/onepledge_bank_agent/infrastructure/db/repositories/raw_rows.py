"""`SqlRawRowRepository`: `loans_raw` (insert-only)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from onepledge_bank_agent.application.ports import IdGenerator, StoredRawRow
from onepledge_bank_agent.domain.tape_file import RawRow
from onepledge_bank_agent.infrastructure.db.models import RawRowRow


class SqlRawRowRepository:
    """Stores raw rows exactly as sent and reads them back in row order."""

    def __init__(self, session: Session, ids: IdGenerator) -> None:
        self._session = session
        self._ids = ids

    def add_many(self, submission_id: uuid.UUID, rows: Sequence[RawRow], received_at: datetime) -> None:
        """One `loans_raw` row per data row."""
        if not rows:
            return
        self._session.execute(
            insert(RawRowRow),
            [
                {
                    "raw_id": self._ids.new_id(),
                    "submission_id": submission_id,
                    "row_number": row.row_number,
                    "raw_payload": dict(row.payload),
                    "row_sha256": row.row_sha256,
                    "received_at": received_at,
                }
                for row in rows
            ],
        )

    def list_for(self, submission_id: uuid.UUID) -> list[StoredRawRow]:
        """Every raw row of a submission, in row number order."""
        statement = (
            select(RawRowRow.raw_id, RawRowRow.row_number, RawRowRow.raw_payload, RawRowRow.row_sha256)
            .where(RawRowRow.submission_id == submission_id)
            .order_by(RawRowRow.row_number)
        )
        return [
            StoredRawRow(row.raw_id, row.row_number, row.raw_payload, row.row_sha256)
            for row in self._session.execute(statement)
        ]
