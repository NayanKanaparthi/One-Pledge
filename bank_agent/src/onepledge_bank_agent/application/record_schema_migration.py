"""`RecordSchemaMigration`: audit an upgrade of the bank database (`onepledge-bank db migrate`).

Written as the owner role through the normal hash chained writer, after a successful
upgrade that changed the revision; an upgrade that found the schema current writes
nothing (SPEC_DECISIONS 30).
"""

from __future__ import annotations

from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.domain.audit import Actor, AuditDraft
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, AuditOutcome


class RecordSchemaMigration:
    """Writes the `SCHEMA_MIGRATED` audit entry."""

    def __init__(self, units: UnitOfWorkFactory, via: str) -> None:
        self._units = units
        self._via = via

    def run(self, from_revision: str | None, to_revision: str) -> bool:
        """Record the change from `from_revision` (None on a new database); False when nothing changed."""
        if from_revision == to_revision:
            return False
        with self._units() as unit:
            unit.record(
                AuditDraft(
                    AuditAction.SCHEMA_MIGRATED,
                    Actor.system(),
                    AuditObjectType.SCHEMA,
                    None,
                    AuditOutcome.SUCCESS,
                    {"via": self._via, "from_revision": from_revision, "to_revision": to_revision},
                )
            )
            unit.commit()
        return True
