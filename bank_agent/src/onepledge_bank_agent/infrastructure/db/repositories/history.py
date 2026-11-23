"""`SqlHistoryReader`: the audit log and the stored tapes, read for deep verification (SPEC_DECISIONS 25)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from onepledge_bank_agent.domain.audit import AuditEntry
from onepledge_bank_agent.domain.enums import TapeKind, TapeSource
from onepledge_bank_agent.domain.raw_evidence import StoredRow, StoredTape
from onepledge_bank_agent.domain.replay import State
from onepledge_bank_agent.domain.stage_digests import DigestRow, StagePart
from onepledge_bank_agent.domain.tape_file import TapeLayout
from onepledge_bank_agent.infrastructure.db.models import LoanRow, RawRowRow, TapeSubmissionRow
from onepledge_bank_agent.infrastructure.db.repositories import stage_rows
from onepledge_bank_agent.infrastructure.db.repositories.audit_log import SqlAuditLogRepository


def _cells(value: object) -> tuple[str, ...]:
    """A stored JSON array of cell texts; anything else reads as no cells (and fails verification)."""
    if not isinstance(value, list) or not all(isinstance(cell, str) for cell in value):
        return ()
    return tuple(value)


class SqlHistoryReader:
    """Reads one submission's facts, layout and raw rows exactly as stored, and the audit log."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = SqlAuditLogRepository(session)

    def audit_entries(self) -> Iterator[AuditEntry]:
        """Every audit entry in seq order, read in batches."""
        return self._audit.entries()

    def audit_size(self) -> tuple[int, int]:
        """Entry count and highest seq."""
        return self._audit.size()

    def submission_ids(self) -> list[uuid.UUID]:
        """Every submission in upload order."""
        statement = select(TapeSubmissionRow.submission_id).order_by(
            TapeSubmissionRow.uploaded_at, TapeSubmissionRow.submission_id
        )
        return list(self._session.execute(statement).scalars())

    def tape(self, submission_id: uuid.UUID) -> StoredTape | None:
        """The stored forms of one submission."""
        tape = self._session.get(TapeSubmissionRow, submission_id)
        if tape is None:
            return None
        statement = (
            select(RawRowRow).where(RawRowRow.submission_id == submission_id).order_by(RawRowRow.row_number)
        )
        rows = tuple(
            StoredRow(row.row_number, row.raw_text, _cells(row.raw_cells), row.raw_payload, row.row_sha256)
            for row in self._session.execute(statement).scalars()
        )
        layout = TapeLayout(tape.has_bom, tape.header_text, _cells(tape.header_cells), tape.trailer_text)
        return StoredTape(
            submission_id=tape.submission_id,
            source=TapeSource(tape.source),
            kind=TapeKind(tape.kind),
            facility_id=tape.facility_id,
            as_of_date=tape.as_of_date,
            file_sha256=tape.file_sha256,
            rows_sha256=tape.rows_sha256,
            row_count=tape.row_count,
            layout=layout,
            rows=rows,
        )

    def loan_ids(self) -> set[uuid.UUID]:
        """The id of every loan."""
        return set(self._session.execute(select(LoanRow.loan_id)).scalars())

    def stage_rows(self, submission_id: uuid.UUID, part: StagePart) -> list[DigestRow]:
        """The rows one stage digest covers."""
        return stage_rows.stage_rows(self._session, submission_id, part)

    def request_rows(self, request_ids: Sequence[uuid.UUID]) -> list[DigestRow]:
        """Requests with their answers and items."""
        return stage_rows.requests(self._session, request_ids)

    def reference_rows(self) -> dict[str, list[DigestRow]]:
        """Lenders and facilities."""
        return stage_rows.reference(self._session)

    def states(self, table: str) -> dict[uuid.UUID, State]:
        """The replayed columns of one table."""
        return stage_rows.states(self._session, table)

    def loan_values(self) -> dict[uuid.UUID, DigestRow]:
        """The derived columns of every loan."""
        return stage_rows.loan_values(self._session)
