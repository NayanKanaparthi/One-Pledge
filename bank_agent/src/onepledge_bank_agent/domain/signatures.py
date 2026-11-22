"""The `SignatureVerifier` port the verification rules are written against (SPEC 15.1).

The registry's Ed25519 public keys come only from the bank's configuration; the rules
never fetch a key. The implementation lives in `infrastructure/crypto/signatures.py`.
"""

from __future__ import annotations

from typing import Protocol


class SignatureVerifier(Protocol):
    """Checks a registry signature against the configured trust anchors."""

    def knows(self, signing_key_id: str) -> bool:
        """True when a public key is configured for this key id."""
        ...

    def verifies(self, signing_key_id: str, message: bytes, signature: str) -> bool:
        """True when `signature` (standard base64) is a valid signature of `message` under that key."""
        ...
