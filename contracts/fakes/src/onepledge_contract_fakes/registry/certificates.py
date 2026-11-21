"""Signed submission certificates (TEST DOUBLE).

`body_hash` is the SHA-256 of the canonical body and `signature` the Ed25519
signature over the same bytes. Item statuses use the certificate words: HELD is a
CONFLICT result.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from onepledge_contract_fakes.canonical_json import JsonValue, canonical_sha256
from onepledge_contract_fakes.errors import CertificateNotFoundError
from onepledge_contract_fakes.registry.ledger import SCHEMA_VERSION
from onepledge_contract_fakes.registry.model import ItemOutcome, ItemResult
from onepledge_contract_fakes.registry.signing import RegistrySigner

CERTIFICATE_STATUS: Mapping[ItemOutcome, str] = MappingProxyType(
    {
        ItemOutcome.LOCKED: "LOCKED",
        ItemOutcome.CONFLICT: "HELD",
        ItemOutcome.RELEASED: "RELEASED",
        ItemOutcome.REJECTED: "REJECTED",
    }
)


@dataclass
class StoredCertificate:
    """A certificate as stored, owned by one member."""

    member_id: str
    body: dict[str, JsonValue]
    body_hash: str
    signature: str
    signing_key_id: str

    def to_json(self) -> dict[str, JsonValue]:
        """The `SignedCertificate` wire shape."""
        return {
            "body": copy.deepcopy(self.body),
            "body_hash": self.body_hash,
            "signature": self.signature,
            "signing_key_id": self.signing_key_id,
        }


@dataclass(frozen=True)
class CertificateFacts:
    """What one certificate states about one submission."""

    certificate_id: str
    member_id: str
    submission_id: str
    kind: str
    key_version: str
    as_of_seq: int
    issued_at: str


def summary_of(results: Sequence[ItemResult]) -> dict[str, JsonValue]:
    """Counts of the certificate summary."""
    statuses = [CERTIFICATE_STATUS[r.result] for r in results]
    return {
        "checked": len(statuses),
        "locked": statuses.count("LOCKED"),
        "held": statuses.count("HELD"),
        "released": statuses.count("RELEASED"),
        "rejected": statuses.count("REJECTED"),
    }


class CertificateIssuer:
    """Builds, signs and keeps certificates."""

    def __init__(self, signer: RegistrySigner) -> None:
        self._signer = signer
        self._certificates: dict[str, StoredCertificate] = {}

    def issue(self, facts: CertificateFacts, results: Sequence[ItemResult]) -> StoredCertificate:
        """Sign and store the certificate of one submission."""
        body: dict[str, JsonValue] = {
            "schema_version": SCHEMA_VERSION,
            "certificate_id": facts.certificate_id,
            "member_id": facts.member_id,
            "submission_id": facts.submission_id,
            "kind": facts.kind,
            "key_version": facts.key_version,
            "as_of_seq": facts.as_of_seq,
            "issued_at": facts.issued_at,
            "summary": summary_of(results),
            "items": [
                {"asset_token": r.asset_token, "status": CERTIFICATE_STATUS[r.result]} for r in results
            ],
        }
        stored = StoredCertificate(
            member_id=facts.member_id,
            body=body,
            body_hash=canonical_sha256(body),
            signature=self._signer.sign(body),
            signing_key_id=self._signer.key_id,
        )
        self._certificates[facts.certificate_id] = stored
        return stored

    def for_member(self, certificate_id: str, member_id: str) -> StoredCertificate:
        """A certificate of this member, or CertificateNotFoundError."""
        stored = self._certificates.get(certificate_id)
        if stored is None or stored.member_id != member_id:
            raise CertificateNotFoundError
        return stored

    def tamper_locked_count(self, certificate_id: str) -> None:
        """Tamper: change `summary.locked` of a stored certificate without re-signing."""
        stored = self._certificates.get(certificate_id)
        if stored is None:
            raise CertificateNotFoundError
        summary = stored.body["summary"]
        if isinstance(summary, dict):
            locked = summary.get("locked")
            summary["locked"] = (locked if isinstance(locked, int) else 0) + 1

    def all(self) -> tuple[StoredCertificate, ...]:
        """Every stored certificate."""
        return tuple(self._certificates.values())
