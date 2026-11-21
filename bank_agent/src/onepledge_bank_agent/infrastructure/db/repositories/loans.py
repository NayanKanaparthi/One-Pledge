"""`SqlLoanRepository`: `loans`."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from onepledge_bank_agent.domain.enums import LoanState
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.infrastructure.db.models import LoanRow
from onepledge_bank_agent.infrastructure.db.repositories.mapping import loan_from_row, loan_values

_MUTABLE = (
    "lender_loan_number",
    "vin",
    "borrower_ref",
    "origination_date",
    "original_amount_cents",
    "current_balance_cents",
    "state",
    "updated_at",
)


class SqlLoanRepository:
    """Loan rows. Every update goes through the loan guard trigger."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def live_by_vin(self, facility_id: uuid.UUID, vins: Iterable[str]) -> dict[str, Loan]:
        """The facility's loans that are not RELEASED, keyed by VIN."""
        wanted = sorted(set(vins))
        if not wanted:
            return {}
        statement = select(LoanRow).where(
            LoanRow.facility_id == facility_id,
            LoanRow.vin.in_(wanted),
            LoanRow.state != LoanState.RELEASED.value,
        )
        return {row.vin: loan_from_row(row) for row in self._session.execute(statement).scalars()}

    def get_many(self, loan_ids: Iterable[uuid.UUID], *, for_update: bool = False) -> dict[uuid.UUID, Loan]:
        """Loans by id, optionally locked (in id order, so two writers never deadlock)."""
        wanted = sorted(set(loan_ids))
        if not wanted:
            return {}
        statement = select(LoanRow).where(LoanRow.loan_id.in_(wanted)).order_by(LoanRow.loan_id)
        if for_update:
            statement = statement.with_for_update()
        return {row.loan_id: loan_from_row(row) for row in self._session.execute(statement).scalars()}

    def add_many(self, loans: Sequence[Loan]) -> None:
        """Insert new loan rows."""
        if loans:
            self._session.execute(insert(LoanRow), [loan_values(loan) for loan in loans])

    def save(self, loan: Loan) -> None:
        """Store the mutable columns of one loan."""
        values = loan_values(loan)
        self._session.execute(
            update(LoanRow)
            .where(LoanRow.loan_id == loan.loan_id)
            .values({name: values[name] for name in _MUTABLE})
        )
