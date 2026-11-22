"""The ports of the shared services and of the cryptography the bank uses with them (SPEC 8.6, 9, 14).

Infrastructure implements them: `crypto/blinder.py` (the only module that imports
`oprf`), `crypto/signatures.py`, `http/key_holder_client.py`, `http/registry_client.py`
and the advisory lock of the outbox sync. Every client raises the named dependency
errors of `domain/dependency_errors.py`; none returns a raw HTTP response.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from onepledge_bank_agent.domain.enums import BankPosition
from onepledge_bank_agent.domain.explanation import AlertFacts, Explanation
from onepledge_bank_agent.domain.registry_records import (
    CaseOutcome,
    Evaluation,
    EventPage,
    OutboxPage,
    RegistryAnswer,
    RegistrySubmission,
    SignedCertificate,
)
from onepledge_bank_agent.domain.signatures import SignatureVerifier

__all__ = [
    "AlertExplainer",
    "Blinded",
    "Blinder",
    "KeyHolderClient",
    "OutboxLock",
    "RegistryClient",
    "SignatureVerifier",
]


class Blinded(Protocol):
    """One blinded element. The blind itself stays inside the object and is never persisted or logged."""

    @property
    def element(self) -> str:
        """The blinded point, standard base64 with padding (44 characters)."""
        ...


class Blinder(Protocol):
    """Blind OPRF on ristretto255 (SPEC 9.3): hash to a point, blind with a fresh scalar, unblind."""

    def blind(self, oprf_input: bytes) -> Blinded:
        """Hash the input bytes to a point and blind it with a fresh random scalar."""
        ...

    def unblind(self, blinded: Blinded, evaluated: str) -> bytes | None:
        """The 32 byte unblinded point of the evaluated element, or None when it is not a valid point."""
        ...


class KeyHolderClient(Protocol):
    """`POST /v1/evaluate` of one key holder."""

    @property
    def dependency(self) -> str:
        """`key_holder_1` or `key_holder_2`, as audit entries and logs name it."""
        ...

    def evaluate(self, elements: Sequence[str]) -> Evaluation:
        """Evaluated elements in request order; raises a key holder error otherwise."""
        ...


class RegistryClient(Protocol):
    """The registry endpoints the bank agent calls (`contracts/registry.yaml`)."""

    def submit(self, idempotency_key: str, submission: RegistrySubmission) -> RegistryAnswer:
        """`POST /v1/submissions`: a 201 decision or a 200 idempotent replay."""
        ...

    def certificate(self, certificate_id: uuid.UUID) -> SignedCertificate:
        """`GET /v1/certificates/{certificate_id}`."""
        ...

    def outbox(self, limit: int) -> OutboxPage:
        """`GET /v1/outbox?limit=`: unacknowledged messages in position order."""
        ...

    def acknowledge(self, message_id: uuid.UUID) -> None:
        """`POST /v1/outbox/{message_id}/ack`."""
        ...

    def record_position(self, case_id: uuid.UUID, position: BankPosition) -> CaseOutcome:
        """`POST /v1/cases/{case_id}/resolution`."""
        ...

    def event_headers(self, after_seq: int, limit: int) -> EventPage:
        """`GET /v1/ledger/events?view=headers&after_seq=&limit=`."""
        ...


class AlertExplainer(Protocol):
    """Writes the plain language text of an alert from facts that hold no loan value (SPEC 14.3)."""

    def explain(self, facts: AlertFacts) -> Explanation:
        """The explanation and who wrote it."""
        ...


class OutboxLock(Protocol):
    """The advisory lock `onepledge:outbox`, so only one outbox sync runs per bank (SPEC 14.1)."""

    def hold(self) -> AbstractContextManager[bool]:
        """Try the lock; the context yields whether it was granted and releases it on exit."""
        ...
