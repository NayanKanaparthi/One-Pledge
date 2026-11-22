"""`SqlEvidenceStores`: the insert-only proof stores of one transaction (SPEC_DECISIONS 67)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from onepledge_bank_agent.infrastructure.db.repositories.certificates import SqlCertificateRepository
from onepledge_bank_agent.infrastructure.db.repositories.checkpoints import SqlCheckpointRepository
from onepledge_bank_agent.infrastructure.db.repositories.integrity_runs import SqlIntegrityRunRepository
from onepledge_bank_agent.infrastructure.db.repositories.quarantine import SqlQuarantineRepository


class SqlEvidenceStores:
    """Certificates, checkpoints seen, quarantined registry messages and integrity runs; nothing mutable."""

    def __init__(self, session: Session) -> None:
        self._certificates = SqlCertificateRepository(session)
        self._checkpoints = SqlCheckpointRepository(session)
        self._quarantine = SqlQuarantineRepository(session)
        self._runs = SqlIntegrityRunRepository(session)

    @property
    def certificates(self) -> SqlCertificateRepository:
        """`certificates`."""
        return self._certificates

    @property
    def checkpoints(self) -> SqlCheckpointRepository:
        """`checkpoints_seen`."""
        return self._checkpoints

    @property
    def quarantine(self) -> SqlQuarantineRepository:
        """`registry_messages_quarantined`."""
        return self._quarantine

    @property
    def runs(self) -> SqlIntegrityRunRepository:
        """`integrity_runs`."""
        return self._runs
