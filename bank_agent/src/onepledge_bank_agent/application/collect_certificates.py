"""`CollectCertificates`: the COMPLETED stage (SPEC 13.2 certify row, 13.6).

For every answered tape request of the submission that has no verified certificate:
fetch, verify and store it. If any certificate of the submission failed verification, the
submission fails with CERTIFICATE_INVALID (resume status SUBMITTED); the loan states are
kept as the registry answered, the failed row stays as evidence, the integrity report's
certificates section turns BROKEN and the halt rule holds (SPEC_DECISIONS 61). The code
is retryable: a retry runs this stage again, which fetches again every request still
without a verified certificate. Otherwise the registry outbox is synced once (so the
alerts and the checkpoint of these decisions arrive now) and the submission is marked
COMPLETED.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from onepledge_bank_agent.application.audit_entries import stage_advanced
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.application.verify_certificate import VerifyCertificate
from onepledge_bank_agent.domain.dependency_errors import CertificateInvalidError
from onepledge_bank_agent.domain.enums import RequestOrigin, TapeStatus


class OutboxSync(Protocol):
    """A quiet registry outbox sync: an outage ends it without raising."""

    def run(self) -> None:
        """Sync once."""
        ...


class CollectCertificates:
    """Runs the certify stage of one SUBMITTED submission."""

    reached = TapeStatus.COMPLETED

    def __init__(
        self, units: UnitOfWorkFactory, verify: VerifyCertificate, sync: OutboxSync, runtime: Runtime
    ) -> None:
        self._units = units
        self._verify = verify
        self._sync = sync
        self._clock = runtime.clock

    def run(self, submission_id: uuid.UUID) -> None:
        """Collect every certificate, then sync the outbox and mark the submission COMPLETED."""
        with self._units() as unit:
            tape = unit.tapes.get(submission_id)
            if tape is None or tape.status is not TapeStatus.SUBMITTED:
                return
            requests = [
                request
                for request in unit.requests.for_submission(submission_id, RequestOrigin.TAPE)
                if request.answered
            ]
        certificates = [self._verify.collect(request) for request in requests]
        if any(not certificate.verified for certificate in certificates):
            raise CertificateInvalidError
        self._sync.run()
        with self._units() as unit:
            tape = unit.tapes.get(submission_id, for_update=True)
            if tape is None or tape.status is not TapeStatus.SUBMITTED:
                return
            before = tape.mark_completed(self._clock.now())
            unit.tapes.save(tape)
            counts = {"certificates_verified": len(certificates)}
            unit.record(stage_advanced(submission_id, before, tape.status, counts))
            unit.commit()
