"""`VerifyCertificate`: fetch, verify and store the certificate of one answered request (SPEC 13.6, 15.2).

The certificate is fetched with no transaction open, checked against the configured
trust anchors and against what the bank sent and was told, and stored with its verdict
in one transaction, whether it verified or not. A request that already has a stored
certificate is not fetched again.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.registry_audit import certificate_checked
from onepledge_bank_agent.application.registry_ports import RegistryClient, SignatureVerifier
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.certificate_rules import CertificateExpectation, verify_certificate
from onepledge_bank_agent.domain.entities import Certificate, RegistryRequest
from onepledge_bank_agent.domain.enums import TokenKind
from onepledge_bank_agent.domain.errors import IllegalTransitionError
from onepledge_bank_agent.domain.registry_records import SignedCertificate
from onepledge_bank_agent.domain.values import Sha256Hex


@dataclass(frozen=True)
class Answered:
    """The stored response facts a certificate is checked against."""

    certificate_id: uuid.UUID
    registry_submission_id: uuid.UUID
    as_of_seq: int


def answered(request: RegistryRequest) -> Answered:
    """The response facts of an answered request."""
    if request.certificate_id is None or request.registry_submission_id is None or request.as_of_seq is None:
        raise IllegalTransitionError("registry_request", "UNANSWERED", "CERTIFY")
    return Answered(request.certificate_id, request.registry_submission_id, request.as_of_seq)


class VerifyCertificate:
    """Collects the certificate of one request and records whether it verified."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        registry: RegistryClient,
        verifier: SignatureVerifier,
        member_id: uuid.UUID,
        runtime: Runtime,
    ) -> None:
        self._units = units
        self._registry = registry
        self._verifier = verifier
        self._member_id = member_id
        self._clock = runtime.clock
        self._faults = runtime.faults
        self._log = runtime.log

    def collect(self, request: RegistryRequest) -> Certificate:
        """The stored certificate of the request, fetched and verified now if it was not stored yet."""
        facts = answered(request)
        with self._units() as unit:
            stored = unit.certificates.for_requests([request.request_id]).get(request.request_id)
        if stored is not None:
            return stored
        signed = self._registry.certificate(facts.certificate_id)
        with self._units() as unit:
            existing = unit.certificates.for_requests([request.request_id]).get(request.request_id)
            if existing is not None:
                return existing
            certificate = self._verified(unit, request, facts, signed)
            unit.certificates.add(certificate)
            failure = certificate.failure.value if certificate.failure else None
            unit.record(certificate_checked(certificate.certificate_id, request, failure, facts.as_of_seq))
            unit.commit()
        self._log.info(
            LogEvent.CERTIFICATE_STORED,
            certificate_id=str(certificate.certificate_id),
            registry_request_id=str(request.request_id),
            result="VERIFIED" if certificate.verified else failure,
        )
        self._faults.reached(FaultPoint.AFTER_CERTIFICATE_STORED)
        return certificate

    def _verified(
        self, unit: UnitOfWork, request: RegistryRequest, facts: Answered, signed: SignedCertificate
    ) -> Certificate:
        items = unit.requests.items(request.request_id)
        tokens = unit.tokens.for_loans(item.loan_id for item in items)
        expected_items = []
        for item in items:
            if item.result is None:
                raise IllegalTransitionError("registry_request_item", "UNANSWERED", "CERTIFY")
            asset = next(
                t.value.value
                for t in tokens[item.loan_id]
                if t.kind is TokenKind.ASSET and t.key_version == request.key_version
            )
            expected_items.append((asset, item.result))
        expectation = CertificateExpectation(
            facts.certificate_id,
            self._member_id,
            facts.registry_submission_id,
            request.kind,
            str(request.key_version),
            facts.as_of_seq,
            tuple(expected_items),
        )
        failure = verify_certificate(signed, expectation, self._verifier)
        now = self._clock.now()
        return Certificate(
            certificate_id=facts.certificate_id,
            request_id=request.request_id,
            registry_submission_id=facts.registry_submission_id,
            kind=request.kind,
            as_of_seq=facts.as_of_seq,
            issued_at=signed.issued_at,
            body=signed.body,
            body_hash=Sha256Hex(signed.body_hash),
            signature=signed.signature,
            signing_key_id=signed.signing_key_id,
            failure=failure,
            verified_at=now,
            received_at=now,
        )
