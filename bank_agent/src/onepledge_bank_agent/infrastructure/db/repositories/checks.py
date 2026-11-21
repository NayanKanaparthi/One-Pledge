"""`SqlCheckRepository`: `local_checks` (insert-only)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from onepledge_bank_agent.application.ports import IdGenerator
from onepledge_bank_agent.domain.enums import CheckType
from onepledge_bank_agent.domain.values import CheckResult
from onepledge_bank_agent.infrastructure.db.models import LocalCheckRow

CHECKS_PER_LOAN = len(CheckType)


class SqlCheckRepository:
    """Stores the four check results of a loan with their reasons and evidence."""

    def __init__(self, session: Session, ids: IdGenerator) -> None:
        self._session = session
        self._ids = ids

    def checked(self, submission_id: uuid.UUID, loan_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """Loans with all four rows for this submission."""
        wanted = sorted(set(loan_ids))
        if not wanted:
            return set()
        statement = (
            select(LocalCheckRow.loan_id)
            .where(LocalCheckRow.submission_id == submission_id, LocalCheckRow.loan_id.in_(wanted))
            .group_by(LocalCheckRow.loan_id)
            .having(func.count() == CHECKS_PER_LOAN)
        )
        return set(self._session.execute(statement).scalars())

    def add(
        self, submission_id: uuid.UUID, loan_id: uuid.UUID, results: Sequence[CheckResult], at: datetime
    ) -> None:
        """Insert the results of one loan."""
        self._session.execute(
            insert(LocalCheckRow),
            [
                {
                    "check_id": self._ids.new_id(),
                    "loan_id": loan_id,
                    "submission_id": submission_id,
                    "check_type": result.check_type.value,
                    "result": result.outcome.value,
                    "reason_code": result.reason.value,
                    "evidence": dict(result.evidence),
                    "checked_at": at,
                }
                for result in results
            ],
        )
