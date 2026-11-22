"""`SqlTapeRepository`: `tape_submissions`."""

from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import and_, exists, insert, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from onepledge_bank_agent.domain.enums import TapeStatus
from onepledge_bank_agent.domain.errors import ConcurrentUploadError
from onepledge_bank_agent.domain.tape import UNFINISHED, TapeSubmission
from onepledge_bank_agent.domain.tape_file import TapeLayout
from onepledge_bank_agent.infrastructure.db.models import TapeSubmissionRow
from onepledge_bank_agent.infrastructure.db.repositories.mapping import tape_from_row, tape_progress_values

FILE_UNIQUE_CONSTRAINT = "uq_tape_submissions_file"


class SqlTapeRepository:
    """Reads and writes submissions in the unit of work's session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, tape: TapeSubmission, layout: TapeLayout) -> None:
        """Insert a RECEIVED submission; the same file stored meanwhile raises `ConcurrentUploadError`."""
        facts = tape.facts
        values = {
            "has_bom": layout.has_bom,
            "header_text": layout.header_text,
            "header_cells": list(layout.header_cells),
            "trailer_text": layout.trailer_text,
            "submission_id": facts.submission_id,
            "facility_id": facts.facility_id,
            "kind": facts.kind.value,
            "source": facts.source.value,
            "file_name": facts.file_name,
            "file_sha256": facts.file_sha256,
            "rows_sha256": facts.rows_sha256,
            "as_of_date": facts.as_of_date,
            "row_count": facts.row_count,
            "uploaded_by": facts.uploaded_by,
            "uploaded_at": facts.uploaded_at,
            **tape_progress_values(tape),
        }
        try:
            self._session.execute(insert(TapeSubmissionRow).values(**values))
        except IntegrityError as error:
            if FILE_UNIQUE_CONSTRAINT in str(error.orig):
                raise ConcurrentUploadError from None
            raise

    def get(self, submission_id: uuid.UUID, *, for_update: bool = False) -> TapeSubmission | None:
        """One submission, optionally locked until the end of the transaction."""
        statement = select(TapeSubmissionRow).where(TapeSubmissionRow.submission_id == submission_id)
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).scalar_one_or_none()
        return tape_from_row(row) if row is not None else None

    def layout(self, submission_id: uuid.UUID) -> TapeLayout | None:
        """The verbatim header, BOM flag and trailer of a submission."""
        statement = select(
            TapeSubmissionRow.has_bom,
            TapeSubmissionRow.header_text,
            TapeSubmissionRow.header_cells,
            TapeSubmissionRow.trailer_text,
        ).where(TapeSubmissionRow.submission_id == submission_id)
        row = self._session.execute(statement).first()
        if row is None:
            return None
        has_bom, header_text, header_cells, trailer_text = row
        return TapeLayout(has_bom, header_text, tuple(header_cells), trailer_text)

    def find_by_file(self, facility_id: uuid.UUID, kind: str, file_sha256: str) -> uuid.UUID | None:
        """The submission that already holds this file for this facility and kind."""
        statement = select(TapeSubmissionRow.submission_id).where(
            TapeSubmissionRow.facility_id == facility_id,
            TapeSubmissionRow.kind == kind,
            TapeSubmissionRow.file_sha256 == file_sha256,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def save(self, tape: TapeSubmission) -> None:
        """Store status, failure handling fields and times."""
        self._session.execute(
            update(TapeSubmissionRow)
            .where(TapeSubmissionRow.submission_id == tape.submission_id)
            .values(**tape_progress_values(tape))
        )

    def runnable(self, statuses: Collection[TapeStatus], now: datetime, limit: int) -> list[uuid.UUID]:
        """Runnable submissions in `(uploaded_at, submission_id)` order, skipping a facility's later tapes."""
        if not statuses:
            return []
        older = aliased(TapeSubmissionRow)
        tape = TapeSubmissionRow
        blocked = exists().where(
            older.facility_id == tape.facility_id,
            older.status.in_([status.value for status in UNFINISHED]),
            tuple_(older.uploaded_at, older.submission_id) < tuple_(tape.uploaded_at, tape.submission_id),
        )
        statement = (
            select(tape.submission_id)
            .where(
                tape.status.in_([status.value for status in statuses]),
                or_(tape.next_attempt_at.is_(None), tape.next_attempt_at <= now),
                and_(~blocked),
            )
            .order_by(tape.uploaded_at, tape.submission_id)
            .limit(limit)
        )
        return list(self._session.execute(statement).scalars())
