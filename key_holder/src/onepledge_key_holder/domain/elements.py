"""A blinded element on the wire, and the scalar rule (`contracts/key_holder.yaml` `Element`).

An element is the 32 byte compressed ristretto255 point in standard base64 with padding. It
is accepted only in its one canonical spelling: the text must re-encode to itself, so
non-zero padding bits are refused, as the layer 1 double refuses them. The identity (32
zero bytes) is never a valid element, in a request or as a result (SPEC 4.2).
"""

from __future__ import annotations

import base64
import binascii

POINT_BYTES = 32
SCALAR_BYTES = 32
MAX_ELEMENTS = 1000
IDENTITY = bytes(POINT_BYTES)


def decode_element(text: str) -> bytes | None:
    """The 32 bytes of a canonical element that is not the identity, or None."""
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    if base64.b64encode(raw).decode("ascii") != text:
        return None
    if len(raw) != POINT_BYTES or raw == IDENTITY:
        return None
    return raw


def encode_element(raw: bytes) -> str:
    """Standard base64 with padding of an evaluated point."""
    return base64.b64encode(raw).decode("ascii")


def is_usable_scalar(raw: bytes) -> bool:
    """SPEC 4.2: a scalar is 32 bytes and not all zeros."""
    return len(raw) == SCALAR_BYTES and raw != bytes(SCALAR_BYTES)
