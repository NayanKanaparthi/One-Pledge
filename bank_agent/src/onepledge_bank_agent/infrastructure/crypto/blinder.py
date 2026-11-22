"""The blind OPRF of SPEC 9.3 on ristretto255 with the `oprf` package. The only module that imports `oprf`.

`blind` hashes the input bytes to a point `P = oprf.data.hash(input)` and multiplies it
by a fresh random scalar `r`: the key holders see only `r * P`. `unblind` divides the
evaluated element by `r`, which gives `k2 * k1 * P` whatever `r` was. The scalar lives
only inside the returned object for the duration of one round: it is never stored,
logged or shown in a `repr`. The scalar source can be injected so the published test
vector, which fixes the blind, can be reproduced exactly.
"""

from __future__ import annotations

import base64
from collections.abc import Callable

import oprf

from onepledge_bank_agent.application.registry_ports import Blinded
from onepledge_bank_agent.infrastructure.encodings import canonical_base64

POINT_BYTES = 32
ENCODED_POINT_LENGTH = 44
_IDENTITY = bytes(POINT_BYTES)


def random_scalar() -> bytes:
    """A fresh random ristretto255 scalar, 32 bytes."""
    return bytes(oprf.mask.random())


class OprfBlinded:
    """One blinded element and, hidden inside, the scalar that blinded it."""

    __slots__ = ("_element", "_scalar")

    def __init__(self, scalar: bytes, element: str) -> None:
        self._scalar = scalar
        self._element = element

    @property
    def element(self) -> str:
        """The blinded point, standard base64 with padding."""
        return self._element

    def unmask(self, evaluated: bytes) -> bytes:
        """Divide an evaluated point by the blinding scalar (raises `RuntimeError` for an invalid point)."""
        return bytes(oprf.mask(self._scalar).unmask(oprf.data(evaluated)))

    def __repr__(self) -> str:
        """Never shows the scalar."""
        return "OprfBlinded(***)"


def decode_point(element: str) -> bytes | None:
    """The 32 bytes of a canonical standard base64 point that is not the identity, or None
    (SPEC_DECISIONS 75: an encoding with non-zero padding bits is refused)."""
    if len(element) != ENCODED_POINT_LENGTH:
        return None
    raw = canonical_base64(element)
    if raw is None or len(raw) != POINT_BYTES or raw == _IDENTITY:
        return None
    return raw


class OprfBlinder:
    """The `Blinder` port on `oprf` 5.0.0."""

    def __init__(self, scalar_source: Callable[[], bytes] = random_scalar) -> None:
        self._scalar_source = scalar_source

    def blind(self, oprf_input: bytes) -> OprfBlinded:
        """`r * hash_to_group(input)` with a fresh scalar `r`."""
        scalar = self._scalar_source()
        point = oprf.data.hash(oprf_input)
        blinded = bytes(oprf.mask(scalar).mask(point))
        return OprfBlinded(scalar, base64.b64encode(blinded).decode("ascii"))

    def unblind(self, blinded: Blinded, evaluated: str) -> bytes | None:
        """`evaluated / r`, or None when the element is not a valid point or the result is the identity."""
        if not isinstance(blinded, OprfBlinded):
            return None
        raw = decode_point(evaluated)
        if raw is None:
            return None
        try:
            point = blinded.unmask(raw)
        except RuntimeError:
            return None
        return None if point == _IDENTITY else point
