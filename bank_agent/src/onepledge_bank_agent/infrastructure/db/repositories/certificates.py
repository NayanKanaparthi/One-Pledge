"""`SqlCertificateRepository`: `certificates` (insert-only, SPEC_DECISIONS 61).

Every certificate received is a row; a failed one stays as evidence beside the verified
one a later fetch stores. The same delivery (certificate id, body hash, signature and
body, index `uq_certificates_delivery`) is stored once: a second insert does nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from onepledge_bank_agent.domain.entities import Certificate
from onepledge_bank_agent.domain.enums import CertificateFailure, TapeKind
from onepledge_bank_agent.domain.values import Sha256Hex
from onepledge_bank_agent.infrastructure.db.models import CertificateRow, RegistryRequestRow


def _certificate(row: CertificateRow) -> Certificate:
    return Certificate(
        certificate_row_id=row.certificate_row_id,
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
    """Stores each received certificate once per delivery, with the bank's verdict."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, certificate: Certificate) -> bool:
        """Insert one certificate; False when this delivery is already stored."""
        statement = (
            insert(CertificateRow)
            .values(
                certificate_row_id=certificate.certificate_row_id,
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
            .on_conflict_do_nothing()
            .returning(CertificateRow.certificate_row_id)
        )
        return self._session.execute(statement).first() is not None

    def verified_for_requests(self, request_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Certificate]:
        """The verified certificate of each request that has one."""
        wanted = sorted(set(request_ids))
        if not wanted:
            return {}
        statement = select(CertificateRow).where(
            CertificateRow.request_id.in_(wanted), CertificateRow.verified
        )
        return {row.request_id: _certificate(row) for row in self._session.execute(statement).scalars()}

    def all(self) -> list[Certificate]:
        """Every row in the order received."""
        statement = select(CertificateRow).order_by(
            CertificateRow.received_at, CertificateRow.certificate_row_id
        )
        return [_certificate(row) for row in self._session.execute(statement).scalars()]

    def expected(self) -> int:
        """Answered registry requests: each names the certificate it should have."""
        statement = select(func.count()).where(RegistryRequestRow.certificate_id.is_not(None))
        return int(self._session.execute(statement).scalar_one())

    def any_unproven(self) -> bool:
        """A request with a failed row and no verified row."""
        verified = aliased(CertificateRow)
        proven = exists().where(verified.request_id == CertificateRow.request_id, verified.verified)
        statement = select(exists().where(CertificateRow.verified.is_(False), ~proven))
        return bool(self._session.execute(statement).scalar_one())
