"""Ed25519 signing of certificates and checkpoints (TEST DOUBLE).

Signatures are over the canonical JSON of the body, standard base64 with
padding. The public key is published to banks out of band (environment).
"""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from onepledge_contract_fakes.canonical_json import JsonValue, canonical_json


class RegistrySigner:
    """Signs canonical JSON bodies with one Ed25519 key."""

    def __init__(self, seed: bytes, key_id: str) -> None:
        self._key = Ed25519PrivateKey.from_private_bytes(seed)
        self.key_id = key_id

    def sign(self, body: JsonValue) -> str:
        """Standard base64 of the signature over the canonical bytes of `body`."""
        return base64.b64encode(self._key.sign(canonical_json(body))).decode("ascii")

    def public_key_b64(self) -> str:
        """Standard base64 of the 32 byte raw public key."""
        raw = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")


def verify_signature(public_key_b64: str, body: JsonValue, signature_b64: str) -> bool:
    """Whether a signature over the canonical bytes of `body` verifies."""
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    try:
        key.verify(base64.b64decode(signature_b64), canonical_json(body))
    except InvalidSignature:
        return False
    return True


def flip_one_bit(signature_b64: str) -> str:
    """The same signature with its first bit inverted (used by the BAD_CHECKPOINT_SIGNATURE tamper)."""
    raw = bytearray(base64.b64decode(signature_b64))
    raw[0] ^= 0x01
    return base64.b64encode(bytes(raw)).decode("ascii")
