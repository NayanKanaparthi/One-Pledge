"""`RecordSchemaMigration`: audit and log an upgrade of the bank database (`onepledge-bank db migrate`).

Written as the owner role through the normal hash chained writer, after a successful
upgrade that changed the revision; an upgrade that found the schema current writes
nothing (SPEC_DECISIONS 30). Each applied revision is also one `schema_migration_applied`
log record with its `revision`, so the operator sees what ran without the library's own
INFO lines (SPEC_DECISIONS 43.6).
"""

from __future__ import annotations

from collections.abc import Sequence

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import OperationalLog, UnitOfWorkFactory
from onepledge_bank_agent.domain.audit import Actor, AuditDraft
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, AuditOutcome


class RecordSchemaMigration:
    """Writes the `SCHEMA_MIGRATED` audit entry and one log record per applied revision."""

    def __init__(self, units: UnitOfWorkFactory, via: str, log: OperationalLog) -> None:
        self._units = units
        self._via = via
        self._log = log

    def run(self, from_revision: str | None, to_revision: str, applied: Sequence[str]) -> bool:
        """Record the change from `from_revision` (None on a new database); False when nothing changed."""
        for revision in applied:
            self._log.info(LogEvent.SCHEMA_MIGRATION_APPLIED, revision=revision)
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
