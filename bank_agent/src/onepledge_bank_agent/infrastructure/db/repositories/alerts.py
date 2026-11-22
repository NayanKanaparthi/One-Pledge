"""`SqlAlertRepository`: `alerts`."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from onepledge_bank_agent.domain.alert import Alert, AlertRecord, MatchFacts, Review
from onepledge_bank_agent.domain.enums import (
    AlertRole,
    AlertStatus,
    BankPosition,
    CaseStatus,
    ExplanationSource,
    MatchBasis,
    MatchKind,
    TokenKind,
)
from onepledge_bank_agent.infrastructure.db.models import AlertRow


def _alert(row: AlertRow) -> Alert:
    match = MatchFacts(
        AlertRole(row.role),
        MatchKind(row.match_kind),
        MatchBasis(row.match_basis),
        tuple(TokenKind(kind) for kind in row.kinds_matched),
    )
    review = Review(
        row.reviewed_by,
        row.reviewed_at,
        row.note,
        BankPosition(row.bank_position) if row.bank_position else None,
        row.position_submitted_at,
    )
    return Alert(
        AlertRecord(
            row.alert_id,
            row.registry_case_id,
            row.loan_id,
            match,
            row.explanation_text,
            ExplanationSource(row.explanation_source),
            AlertStatus(row.status),
            CaseStatus(row.case_status),
            row.received_at,
            review,
        )
    )


def _review_values(alert: Alert) -> dict[str, object]:
    record = alert.record
    return {
        "status": record.status.value,
        "case_status": record.case_status.value,
        "bank_position": record.review.bank_position.value if record.review.bank_position else None,
        "position_submitted_at": record.review.position_submitted_at,
        "note": record.review.note,
        "reviewed_by": record.review.reviewed_by,
        "reviewed_at": record.review.reviewed_at,
    }


class SqlAlertRepository:
    """Alerts keyed by case and loan."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, alert_id: uuid.UUID, *, for_update: bool = False) -> Alert | None:
        """One alert, optionally locked."""
        statement = select(AlertRow).where(AlertRow.alert_id == alert_id)
        if for_update:
            statement = statement.with_for_update()
        row = self._session.execute(statement).scalar_one_or_none()
        return _alert(row) if row is not None else None

    def find(self, registry_case_id: uuid.UUID, loan_id: uuid.UUID) -> Alert | None:
        """The alert of a case on a loan."""
        statement = select(AlertRow).where(
            AlertRow.registry_case_id == registry_case_id, AlertRow.loan_id == loan_id
        )
        row = self._session.execute(statement).scalar_one_or_none()
        return _alert(row) if row is not None else None

    def for_case(self, registry_case_id: uuid.UUID, loan_ids: Iterable[uuid.UUID]) -> list[Alert]:
        """The case's alerts on these loans, locked for update."""
        wanted = sorted(set(loan_ids))
        if not wanted:
            return []
        statement = (
            select(AlertRow)
            .where(AlertRow.registry_case_id == registry_case_id, AlertRow.loan_id.in_(wanted))
            .order_by(AlertRow.alert_id)
            .with_for_update()
        )
        return [_alert(row) for row in self._session.execute(statement).scalars()]

    def for_loan(self, loan_id: uuid.UUID) -> list[Alert]:
        """Every alert on a loan, oldest first."""
        statement = (
            select(AlertRow)
            .where(AlertRow.loan_id == loan_id)
            .order_by(AlertRow.received_at, AlertRow.alert_id)
        )
        return [_alert(row) for row in self._session.execute(statement).scalars()]

    def add(self, alert: Alert) -> None:
        """Insert a new alert."""
        record = alert.record
        self._session.execute(
            insert(AlertRow).values(
                alert_id=record.alert_id,
                registry_case_id=record.registry_case_id,
                loan_id=record.loan_id,
                role=record.match.role.value,
                match_kind=record.match.match_kind.value,
                match_basis=record.match.match_basis.value,
                kinds_matched=[kind.value for kind in record.match.kinds_matched],
                kinds_not_matched=[kind.value for kind in record.match.kinds_not_matched],
                explanation_text=record.explanation_text,
                explanation_source=record.explanation_source.value,
                received_at=record.received_at,
                **_review_values(alert),
            )
        )

    def save(self, alert: Alert) -> None:
        """Store the statuses and review fields."""
        self._session.execute(
            update(AlertRow).where(AlertRow.alert_id == alert.record.alert_id).values(**_review_values(alert))
        )
