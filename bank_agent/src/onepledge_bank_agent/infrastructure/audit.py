"""The hash chained audit writer (SPEC 17.1).

Inside the caller's transaction it takes the advisory lock `onepledge:audit`, reads the
last `(seq, entry_hash)`, and inserts the queued entries, each linked to the one before.
The database trigger `onepledge_audit_guard()` refuses any entry that does not extend
the chain, so a writer that skipped the lock could not break it.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from onepledge_bank_agent.application.ports import Clock, IdGenerator
from onepledge_bank_agent.domain.audit import AuditDraft, AuditEntry, chain_entry
from onepledge_bank_agent.infrastructure.db.models import AuditLogRow

AUDIT_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended('onepledge:audit', 0))")


class HashChainedAuditWriter:
    """Appends audit entries to the chain as the last statements of a transaction."""

    def __init__(self, clock: Clock, ids: IdGenerator) -> None:
        self._clock = clock
        self._ids = ids

    def append(self, session: Session, drafts: Sequence[AuditDraft]) -> list[AuditEntry]:
        """Link and insert the drafts in order; returns the stored entries."""
        if not drafts:
            return []
        session.execute(AUDIT_LOCK)
        last = session.execute(
            select(AuditLogRow.seq, AuditLogRow.entry_hash).order_by(AuditLogRow.seq.desc()).limit(1)
        ).first()
        previous = (last.seq, last.entry_hash) if last is not None else None
        at = self._clock.now()
        entries: list[AuditEntry] = []
        for draft in drafts:
            entry = chain_entry(draft, self._ids.new_id(), at, previous)
            entries.append(entry)
            previous = (entry.seq, entry.entry_hash)
        session.execute(
            insert(AuditLogRow),
            [
                {
                    "audit_id": e.audit_id,
                    "seq": e.seq,
                    "at": e.at,
                    "actor_type": e.actor_type.value,
                    "actor_user_id": e.actor_user_id,
                    "action": e.action,
                    "object_type": e.object_type,
                    "object_id": e.object_id,
                    "outcome": e.outcome,
                    "request_id": e.request_id,
                    "detail": dict(e.detail),
                    "prev_hash": e.prev_hash,
                    "entry_hash": e.entry_hash,
                }
                for e in entries
            ],
        )
        return entries
