"""`SqlSubmissionItemRepository`: `submission_items` and `cleaning_issues` (both insert-only)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from onepledge_bank_agent.application.ports import AcceptedItem, IdGenerator, NewItem
from onepledge_bank_agent.domain.enums import RowDisposition
from onepledge_bank_agent.infrastructure.db.models import (
    CleaningIssueRow,
    SubmissionItemRow,
    TapeSubmissionRow,
)

CHECKED_DISPOSITIONS = (RowDisposition.ACCEPTED_NEW.value, RowDisposition.ACCEPTED_REPRESENTED.value)


class SqlSubmissionItemRepository:
    """Stores the per row outcome of the clean stage and reads the items the check stage needs."""

    def __init__(self, session: Session, ids: IdGenerator) -> None:
        self._session = session
        self._ids = ids

    def add_many(self, submission_id: uuid.UUID, items: Sequence[NewItem]) -> None:
        """Insert every item, then every cleaning issue of every item."""
        if not items:
            return
        self._session.execute(
            insert(SubmissionItemRow),
            [
                {
                    "item_id": item.item_id,
                    "submission_id": submission_id,
                    "raw_id": item.raw_id,
                    "row_number": item.row_number,
                    "disposition": item.disposition.value,
                    "loan_id": item.loan_id,
                }
                for item in items
            ],
        )
        issues = [
            {
                "issue_id": self._ids.new_id(),
                "raw_id": item.raw_id,
                "field": issue.field,
                "problem": issue.problem.value,
                "action": issue.action.value,
            }
            for item in items
            for issue in item.issues
        ]
        if issues:
            self._session.execute(insert(CleaningIssueRow), issues)

    def accepted(self, submission_id: uuid.UUID) -> list[AcceptedItem]:
        """Accepted items with their loans, in row number order."""
        statement = (
            select(SubmissionItemRow.row_number, SubmissionItemRow.loan_id)
            .where(
                SubmissionItemRow.submission_id == submission_id,
                SubmissionItemRow.disposition.in_(CHECKED_DISPOSITIONS),
            )
            .order_by(SubmissionItemRow.row_number)
        )
        return [
            AcceptedItem(row.row_number, row.loan_id)
            for row in self._session.execute(statement)
            if row.loan_id is not None
        ]

    def release_requested(self, submission_id: uuid.UUID) -> list[AcceptedItem]:
        """Items to release with their loans, in row number order."""
        statement = (
            select(SubmissionItemRow.row_number, SubmissionItemRow.loan_id)
            .where(
                SubmissionItemRow.submission_id == submission_id,
                SubmissionItemRow.disposition == RowDisposition.RELEASE_REQUESTED.value,
            )
            .order_by(SubmissionItemRow.row_number)
        )
        return [
            AcceptedItem(row.row_number, row.loan_id)
            for row in self._session.execute(statement)
            if row.loan_id is not None
        ]

    def latest_submission_of(self, loan_id: uuid.UUID) -> uuid.UUID | None:
        """The most recently uploaded submission with an item for this loan."""
        statement = (
            select(SubmissionItemRow.submission_id)
            .join(TapeSubmissionRow, TapeSubmissionRow.submission_id == SubmissionItemRow.submission_id)
            .where(SubmissionItemRow.loan_id == loan_id)
            .order_by(TapeSubmissionRow.uploaded_at.desc(), TapeSubmissionRow.submission_id.desc())
            .limit(1)
        )
        found: uuid.UUID | None = self._session.execute(statement).scalar_one_or_none()
        return found
