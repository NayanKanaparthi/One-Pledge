"""The SQL unit of work: one transaction, every repository, audit entries appended before commit."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from onepledge_bank_agent.application.ports import IdGenerator
from onepledge_bank_agent.domain.audit import AuditDraft
from onepledge_bank_agent.infrastructure.audit import HashChainedAuditWriter
from onepledge_bank_agent.infrastructure.db.refusals import store_error
from onepledge_bank_agent.infrastructure.db.repositories.alerts import SqlAlertRepository
from onepledge_bank_agent.infrastructure.db.repositories.checks import SqlCheckRepository
from onepledge_bank_agent.infrastructure.db.repositories.evidence_stores import SqlEvidenceStores
from onepledge_bank_agent.infrastructure.db.repositories.facilities import SqlFacilityRepository
from onepledge_bank_agent.infrastructure.db.repositories.history import SqlHistoryReader
from onepledge_bank_agent.infrastructure.db.repositories.items import SqlSubmissionItemRepository
from onepledge_bank_agent.infrastructure.db.repositories.loans import SqlLoanRepository
from onepledge_bank_agent.infrastructure.db.repositories.outbox_ledger import (
    SqlOutboxLedgerRepository,
    SqlOutboxStateRepository,
)
from onepledge_bank_agent.infrastructure.db.repositories.raw_rows import SqlRawRowRepository
from onepledge_bank_agent.infrastructure.db.repositories.requests import SqlRegistryRequestRepository
from onepledge_bank_agent.infrastructure.db.repositories.tapes import SqlTapeRepository
from onepledge_bank_agent.infrastructure.db.repositories.tokens import SqlTokenRepository


class SqlUnitOfWork:
    """A transaction on one connection. Leaving without `commit()` rolls back."""

    tapes: SqlTapeRepository
    raw_rows: SqlRawRowRepository
    items: SqlSubmissionItemRepository
    loans: SqlLoanRepository
    checks: SqlCheckRepository
    tokens: SqlTokenRepository
    facilities: SqlFacilityRepository
    requests: SqlRegistryRequestRepository
    alerts: SqlAlertRepository
    outbox_ledger: SqlOutboxLedgerRepository
    outbox_state: SqlOutboxStateRepository
    evidence: SqlEvidenceStores
    history: SqlHistoryReader

    def __init__(self, engine: Engine, writer: HashChainedAuditWriter, ids: IdGenerator) -> None:
        self._engine = engine
        self._writer = writer
        self._ids = ids
        self._drafts: list[AuditDraft] = []
        self._session: Session | None = None

    def __enter__(self) -> Self:
        """Open the session and bind the repositories to it."""
        session = Session(self._engine, expire_on_commit=False, autoflush=False)
        self._session = session
        self.tapes = SqlTapeRepository(session)
        self.raw_rows = SqlRawRowRepository(session, self._ids)
        self.items = SqlSubmissionItemRepository(session, self._ids)
        self.loans = SqlLoanRepository(session)
        self.checks = SqlCheckRepository(session, self._ids)
        self.tokens = SqlTokenRepository(session)
        self.facilities = SqlFacilityRepository(session)
        self.requests = SqlRegistryRequestRepository(session)
        self.alerts = SqlAlertRepository(session)
        self.outbox_ledger = SqlOutboxLedgerRepository(session)
        self.outbox_state = SqlOutboxStateRepository(session)
        self.evidence = SqlEvidenceStores(session)
        self.history = SqlHistoryReader(session)
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        """Roll back whatever was not committed and release the connection; a driver error that is a
        refusal or a passing failure leaves as the store error it stands for (SPEC_DECISIONS 92)."""
        session, self._session = self._session, None
        self._drafts.clear()
        if session is not None:
            try:
                session.rollback()
            except DBAPIError:
                if not isinstance(error, DBAPIError):
                    raise
            finally:
                session.close()
        if isinstance(error, DBAPIError):
            mapped = store_error(error)
            if mapped is not None:
                raise mapped from error

    def record(self, draft: AuditDraft) -> None:
        """Queue an audit entry for this transaction."""
        self._drafts.append(draft)

    def commit(self) -> None:
        """Append the queued audit entries, hash chained, then commit."""
        if self._session is None:
            raise RuntimeError("the unit of work is not open")
        self._writer.append(self._session, self._drafts)
        try:
            self._session.commit()
        except DBAPIError as error:
            mapped = store_error(error)
            if mapped is None:
                raise
            raise mapped from error
        self._drafts.clear()


class SqlUnitOfWorkFactory:
    """Makes a new unit of work per call (the `UnitOfWorkFactory` port)."""

    def __init__(self, engine: Engine, writer: HashChainedAuditWriter, ids: IdGenerator) -> None:
        self._engine = engine
        self._writer = writer
        self._ids = ids

    def __call__(self) -> SqlUnitOfWork:
        """A unit of work that is not open yet."""
        return SqlUnitOfWork(self._engine, self._writer, self._ids)
