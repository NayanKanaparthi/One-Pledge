"""Verification of a registry certificate (SPEC 15.2), as pure rules over an injected `SignatureVerifier`.

In this order, the first failure is the reason:

1. the signing key id is configured, else UNKNOWN_SIGNING_KEY;
2. the delivered body's hash, `sha256(canonical_json(body))` as the bank computed it
   (`delivered_body_sha256`, SPEC_DECISIONS 64), equals the claimed `body_hash`, else
   BODY_HASH_MISMATCH;
3. the Ed25519 signature over `canonical_json(body)` verifies, else BAD_SIGNATURE;
4. the body states what the bank sent and was told, else CONTENT_MISMATCH.

The caller computes the delivered hash once with `delivered_sha256` and passes it in, so
the value a verdict rests on is the value stored. Deep verification recomputes it from
the stored body and re-runs steps 1 to 3 on every stored verified certificate (SPEC 15.6).
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from onepledge_bank_agent.domain.canonical_json import JsonValue, canonical_json, sha256_hex
from onepledge_bank_agent.domain.enums import CertificateFailure, RegistryItemResult, TapeKind
from onepledge_bank_agent.domain.errors import NonCanonicalValueError
from onepledge_bank_agent.domain.registry_records import SignedCertificate
from onepledge_bank_agent.domain.signatures import SignatureVerifier

CERTIFICATE_WORDS: Mapping[RegistryItemResult, str] = MappingProxyType(
    {
        RegistryItemResult.LOCKED: "LOCKED",
        RegistryItemResult.CONFLICT: "HELD",
        RegistryItemResult.RELEASED: "RELEASED",
        RegistryItemResult.REJECTED: "REJECTED",
    }
)
SUMMARY_KEYS: Mapping[str, str] = MappingProxyType(
    {"LOCKED": "locked", "HELD": "held", "RELEASED": "released", "REJECTED": "rejected"}
)


@dataclass(frozen=True)
class CertificateExpectation:
    """What the bank sent and was told for one request, in item position order."""

    certificate_id: uuid.UUID
    member_id: uuid.UUID
    registry_submission_id: uuid.UUID
    kind: TapeKind
    key_version: str
    as_of_seq: int
    items: tuple[tuple[str, RegistryItemResult], ...]


def canonical_body(body: Mapping[str, JsonValue]) -> bytes | None:
    """The canonical bytes of a body, or None when the body holds a value canonical JSON refuses."""
    try:
        return canonical_json(dict(body))
    except NonCanonicalValueError:
        return None


def delivered_sha256(body: Mapping[str, JsonValue]) -> str | None:
    """SHA-256 of the canonical JSON of a body as delivered, or None when it is not canonical JSON."""
    message = canonical_body(body)
    return None if message is None else sha256_hex(message)


def verify_proof(
    certificate: SignedCertificate, delivered: str | None, verifier: SignatureVerifier
) -> CertificateFailure | None:
    """Steps 1 to 3: key id known, delivered hash equal to the claimed one, signature."""
    if not verifier.knows(certificate.signing_key_id):
        return CertificateFailure.UNKNOWN_SIGNING_KEY
    message = canonical_body(certificate.body)
    if message is None or delivered is None or delivered != certificate.body_hash:
        return CertificateFailure.BODY_HASH_MISMATCH
    if not verifier.verifies(certificate.signing_key_id, message, certificate.signature):
        return CertificateFailure.BAD_SIGNATURE
    return None


def verify_certificate(
    certificate: SignedCertificate,
    delivered: str | None,
    expected: CertificateExpectation,
    verifier: SignatureVerifier,
) -> CertificateFailure | None:
    """All four steps; None when the certificate is verified."""
    failure = verify_proof(certificate, delivered, verifier)
    if failure is not None:
        return failure
    if not content_matches(certificate.body, expected):
        return CertificateFailure.CONTENT_MISMATCH
    return None


def _same_id(value: JsonValue, expected: uuid.UUID) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return uuid.UUID(value) == expected
    except ValueError:
        return False


def _items(value: JsonValue) -> list[tuple[str, str]] | None:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return None
    found: list[tuple[str, str]] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            return None
        token, status = entry.get("asset_token"), entry.get("status")
        if not isinstance(token, str) or not isinstance(status, str):
            return None
        found.append((token, status))
    return found


def _summary_matches(value: JsonValue, statuses: Sequence[str]) -> bool:
    if not isinstance(value, Mapping):
        return False
    counts = Counter(statuses)
    expected = {"checked": len(statuses), **{key: counts[word] for word, key in SUMMARY_KEYS.items()}}
    return dict(value) == expected


def content_matches(body: Mapping[str, JsonValue], expected: CertificateExpectation) -> bool:
    """Step 4: ids, member, kind, key version, `as_of_seq`, the items in order and the summary."""
    as_of_seq = body.get("as_of_seq")
    items = _items(body.get("items"))
    wanted = [(token, CERTIFICATE_WORDS[result]) for token, result in expected.items]
    return (
        _same_id(body.get("certificate_id"), expected.certificate_id)
        and _same_id(body.get("member_id"), expected.member_id)
        and _same_id(body.get("submission_id"), expected.registry_submission_id)
        and body.get("kind") == expected.kind.value
        and body.get("key_version") == expected.key_version
        and isinstance(as_of_seq, int)
        and not isinstance(as_of_seq, bool)
        and as_of_seq == expected.as_of_seq
        and items == wanted
        and _summary_matches(body.get("summary"), [status for _, status in wanted])
    )
