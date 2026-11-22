"""`SqlRegistryRequestRepository`: `registry_requests` and `registry_request_items`."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from onepledge_bank_agent.application.evidence_ports import ItemRecord, PlannedItem
from onepledge_bank_agent.domain.entities import RegistryRequest
from onepledge_bank_agent.domain.enums import RegistryItemResult, RequestOrigin, TapeKind
from onepledge_bank_agent.domain.registry_records import ItemOutcome, RegistryAnswer
from onepledge_bank_agent.domain.registry_results import StoredResult
from onepledge_bank_agent.domain.values import KeyVersion, Sha256Hex
from onepledge_bank_agent.infrastructure.db.models import RegistryRequestItemRow, RegistryRequestRow


def _request(row: RegistryRequestRow) -> RegistryRequest:
    return RegistryRequest(
        request_id=row.request_id,
        submission_id=row.submission_id,
        origin=RequestOrigin(row.origin),
        batch_index=row.batch_index,
        idempotency_key=Sha256Hex(row.idempotency_key),
        kind=TapeKind(row.kind),
        key_version=KeyVersion.parse(row.key_version),
        item_count=row.item_count,
        attempts=row.attempts,
        response_status=row.response_status,
        certificate_id=row.certificate_id,
        registry_submission_id=row.registry_submission_id,
        as_of_seq=row.as_of_seq,
    )


class SqlRegistryRequestRepository:
    """Planned requests, their stored responses and per item results."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, request: RegistryRequest, items: Sequence[PlannedItem]) -> None:
        """Insert the request and its items."""
        self._session.execute(
            insert(RegistryRequestRow).values(
                request_id=request.request_id,
                submission_id=request.submission_id,
                origin=request.origin.value,
                batch_index=request.batch_index,
                idempotency_key=request.idempotency_key.value,
                kind=request.kind.value,
                key_version=str(request.key_version),
                item_count=request.item_count,
                attempts=request.attempts,
            )
        )
        self._session.execute(
            insert(RegistryRequestItemRow),
            [
                {
                    "request_id": request.request_id,
                    "item_ref": item.item_ref,
                    "position": item.position,
                    "loan_id": item.loan_id,
                }
                for item in items
            ],
        )

    def for_submission(self, submission_id: uuid.UUID, origin: RequestOrigin) -> list[RegistryRequest]:
        """A submission's requests of one origin in batch order."""
        statement = (
            select(RegistryRequestRow)
            .where(
                RegistryRequestRow.submission_id == submission_id, RegistryRequestRow.origin == origin.value
            )
            .order_by(RegistryRequestRow.batch_index, RegistryRequestRow.request_id)
        )
        return [_request(row) for row in self._session.execute(statement).scalars()]

    def get(self, request_id: uuid.UUID) -> RegistryRequest | None:
        """One request."""
        row = self._session.get(RegistryRequestRow, request_id)
        return _request(row) if row is not None else None

    def by_key(self, idempotency_key: str) -> RegistryRequest | None:
        """The request with this idempotency key."""
        statement = select(RegistryRequestRow).where(RegistryRequestRow.idempotency_key == idempotency_key)
        row = self._session.execute(statement).scalar_one_or_none()
        return _request(row) if row is not None else None

    def items(self, request_id: uuid.UUID) -> list[ItemRecord]:
        """Items in position order."""
        statement = (
            select(RegistryRequestItemRow)
            .where(RegistryRequestItemRow.request_id == request_id)
            .order_by(RegistryRequestItemRow.position)
        )
        return [
            ItemRecord(
                row.item_ref,
                row.position,
                row.loan_id,
                RegistryItemResult(row.result) if row.result is not None else None,
                row.registry_case_id,
            )
            for row in self._session.execute(statement).scalars()
        ]

    def mark_sent(self, request_id: uuid.UUID, at: datetime) -> int:
        """One more attempt; `sent_at` keeps the first."""
        statement = (
            update(RegistryRequestRow)
            .where(RegistryRequestRow.request_id == request_id)
            .values(
                attempts=RegistryRequestRow.attempts + 1,
                sent_at=func.coalesce(RegistryRequestRow.sent_at, at),
                last_attempt_at=at,
            )
            .returning(RegistryRequestRow.attempts)
        )
        attempts: int = self._session.execute(statement).scalar_one()
        return attempts

    def store_answer(self, request_id: uuid.UUID, answer: RegistryAnswer, at: datetime) -> None:
        """The response facts and its body (codes and registry ids only)."""
        self._session.execute(
            update(RegistryRequestRow)
            .where(RegistryRequestRow.request_id == request_id)
            .values(
                response_status=answer.status,
                responded_at=at,
                registry_submission_id=answer.submission_id,
                as_of_seq=answer.as_of_seq,
                certificate_id=answer.certificate_id,
                response_body=dict(answer.body),
            )
        )

    def store_result(self, request_id: uuid.UUID, outcome: ItemOutcome, stored: StoredResult) -> None:
        """One item's stored result."""
        self._session.execute(
            update(RegistryRequestItemRow)
            .where(
                RegistryRequestItemRow.request_id == request_id,
                RegistryRequestItemRow.item_ref == outcome.item_ref,
            )
            .values(
                result=stored.result.value,
                already_held=outcome.already_held,
                match_kind=outcome.match_kind.value if outcome.match_kind else None,
                match_basis=outcome.match_basis.value if outcome.match_basis else None,
                kinds_matched=[kind.value for kind in outcome.kinds_matched],
                registry_case_id=outcome.case_id,
                lock_id=outcome.lock_id,
                reason=stored.reason,
            )
        )
