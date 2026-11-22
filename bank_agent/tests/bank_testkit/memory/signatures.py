"""`FakeSignatures`: a signer and a `SignatureVerifier` over a keyed digest, no key material needed.

A signature is the base64 SHA-256 of the key id, a separator and the message, so the
fake registry signs exactly what the bank verifies: any changed byte of the message,
another key id or a flipped signature fails, as with Ed25519. Only key ids in `known`
are trusted (UNKNOWN_SIGNING_KEY otherwise).
"""

from __future__ import annotations

import base64
import hashlib
import hmac

REGISTRY_KEY_ID = "registry-memory-1"


def sign(key_id: str, message: bytes) -> str:
    """The signature of `message` under `key_id`."""
    return base64.b64encode(hashlib.sha256(key_id.encode() + b"|" + message).digest()).decode("ascii")


class FakeSignatures:
    """Verifies the signatures `sign` makes, for the key ids it was told to trust."""

    def __init__(self, known: frozenset[str] = frozenset({REGISTRY_KEY_ID})) -> None:
        self.known = set(known)

    def knows(self, signing_key_id: str) -> bool:
        """True for a trusted key id."""
        return signing_key_id in self.known

    def verifies(self, signing_key_id: str, message: bytes, signature: str) -> bool:
        """True when `signature` is `sign(signing_key_id, message)`."""
        return hmac.compare_digest(sign(signing_key_id, message), signature)
