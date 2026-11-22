"""`SqlAuditLogRepository`: reads of `audit_log` (entries are appended by the hash chained writer)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from onepledge_bank_agent.domain.audit import AuditEntry
from onepledge_bank_agent.domain.enums import ActorType
from onepledge_bank_agent.infrastructure.db.models import AuditLogRow

BATCH = 1000


class SqlAuditLogRepository:
    """Every entry in seq order, read in batches so a long log is never loaded at once."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def entries(self) -> Iterator[AuditEntry]:
        """Every entry in seq order."""
        after = 0
        while True:
            statement = (
                select(AuditLogRow).where(AuditLogRow.seq > after).order_by(AuditLogRow.seq).limit(BATCH)
            )
            rows = list(self._session.execute(statement).scalars())
            for row in rows:
                yield AuditEntry(
                    audit_id=row.audit_id,
                    seq=row.seq,
                    at=row.at,
                    actor_type=ActorType(row.actor_type),
                    actor_user_id=row.actor_user_id,
                    action=row.action,
                    object_type=row.object_type,
                    object_id=row.object_id,
                    outcome=row.outcome,
                    request_id=row.request_id,
                    detail=row.detail,
                    prev_hash=row.prev_hash,
                    entry_hash=row.entry_hash,
                )
            if len(rows) < BATCH:
                return
            after = rows[-1].seq

    def size(self) -> tuple[int, int]:
        """Entry count and highest seq."""
        count, last = self._session.execute(
            select(func.count(), func.coalesce(func.max(AuditLogRow.seq), 0))
        ).one()
        return int(count), int(last)
