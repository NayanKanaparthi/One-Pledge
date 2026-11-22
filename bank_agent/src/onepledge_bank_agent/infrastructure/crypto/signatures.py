"""Ed25519 verification of registry certificates and checkpoints (SPEC 15.1) with `cryptography`.

The public keys are the trust anchors of `REGISTRY_PUBLIC_KEYS`, read from the
environment: a key id mapped to the standard base64 of a 32 byte raw Ed25519 public
key. The bank never fetches a key from the registry.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from types import MappingProxyType

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from onepledge_bank_agent.infrastructure.operator_errors import CONFIGURATION_ERROR, OperatorError

PUBLIC_KEY_BYTES = 32
SIGNATURE_BYTES = 64


def _decode(text: str) -> bytes | None:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None


def public_key(text: str) -> Ed25519PublicKey:
    """A raw Ed25519 public key from standard base64; one sentence naming the variable otherwise."""
    raw = _decode(text)
    if raw is None or len(raw) != PUBLIC_KEY_BYTES:
        raise OperatorError(
            "Configuration error: REGISTRY_PUBLIC_KEYS must map key ids to 32 byte base64 public keys.",
            CONFIGURATION_ERROR,
        )
    return Ed25519PublicKey.from_public_bytes(raw)


class Ed25519SignatureVerifier:
    """The `SignatureVerifier` port over the configured registry public keys."""

    def __init__(self, public_keys: Mapping[str, str]) -> None:
        self._keys = MappingProxyType({key_id: public_key(text) for key_id, text in public_keys.items()})

    def knows(self, signing_key_id: str) -> bool:
        """True when this key id is configured."""
        return signing_key_id in self._keys

    def verifies(self, signing_key_id: str, message: bytes, signature: str) -> bool:
        """True when the signature over `message` verifies under the configured key."""
        key = self._keys.get(signing_key_id)
        raw = _decode(signature)
        if key is None or raw is None or len(raw) != SIGNATURE_BYTES:
            return False
        try:
            key.verify(raw, message)
        except InvalidSignature:
            return False
        return True
