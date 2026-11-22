"""`SqlCertificateRepository`: `certificates` (insert-only)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from onepledge_bank_agent.domain.entities import Certificate
from onepledge_bank_agent.domain.enums import CertificateFailure, TapeKind
from onepledge_bank_agent.domain.values import Sha256Hex
from onepledge_bank_agent.infrastructure.db.models import CertificateRow


def _certificate(row: CertificateRow) -> Certificate:
    return Certificate(
        certificate_id=row.certificate_id,
        request_id=row.request_id,
        registry_submission_id=row.registry_submission_id,
        kind=TapeKind(row.kind),
        as_of_seq=row.as_of_seq,
        issued_at=row.issued_at,
        body=row.body,
        body_hash=Sha256Hex(row.body_hash),
        signature=row.signature,
        signing_key_id=row.signing_key_id,
        failure=CertificateFailure(row.failure_reason) if row.failure_reason else None,
        verified_at=row.verified_at,
        received_at=row.received_at,
    )


class SqlCertificateRepository:
    """Stores each received certificate once, with the bank's verdict."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, certificate: Certificate) -> None:
        """Insert one certificate."""
        self._session.execute(
            insert(CertificateRow).values(
                certificate_id=certificate.certificate_id,
                request_id=certificate.request_id,
                registry_submission_id=certificate.registry_submission_id,
                kind=certificate.kind.value,
                as_of_seq=certificate.as_of_seq,
                issued_at=certificate.issued_at,
                body=dict(certificate.body),
                body_hash=certificate.body_hash.value,
                signature=certificate.signature,
                signing_key_id=certificate.signing_key_id,
                verified=certificate.verified,
                verified_at=certificate.verified_at,
                failure_reason=certificate.failure.value if certificate.failure else None,
                received_at=certificate.received_at,
            )
        )

    def for_requests(self, request_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Certificate]:
        """Certificates by request id."""
        wanted = sorted(set(request_ids))
        if not wanted:
            return {}
        statement = select(CertificateRow).where(CertificateRow.request_id.in_(wanted))
        return {row.request_id: _certificate(row) for row in self._session.execute(statement).scalars()}

    def all(self) -> list[Certificate]:
        """Every certificate in the order received."""
        statement = select(CertificateRow).order_by(CertificateRow.received_at, CertificateRow.certificate_id)
        return [_certificate(row) for row in self._session.execute(statement).scalars()]
