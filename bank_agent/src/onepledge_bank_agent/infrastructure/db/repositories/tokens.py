"""`SqlTokenRepository`: `loan_tokens` (insert-only)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from onepledge_bank_agent.infrastructure.db.models import LoanTokenRow


class SqlTokenRepository:
    """Reads which loans already carry codes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def loans_with_tokens(self, loan_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """The loans that have at least one stored token, of any key version."""
        wanted = sorted(set(loan_ids))
        if not wanted:
            return set()
        statement = select(LoanTokenRow.loan_id).where(LoanTokenRow.loan_id.in_(wanted)).distinct()
        return set(self._session.execute(statement).scalars())
