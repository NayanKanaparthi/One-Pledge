"""The secret scalar in memory, and the one place the `oprf` package is called (SPEC 4.2).

The scalar comes from the environment through the settings and is held only inside
`oprf.mask`. It is never written to a table, a log line or a `repr`.
"""

from __future__ import annotations

from collections.abc import Mapping

import oprf

from onepledge_key_holder.domain.elements import is_usable_scalar
from onepledge_key_holder.domain.identity import KeyVersion


class UnusableScalarError(ValueError):
    """A scalar that is not 32 bytes or is all zeros (SPEC 4.2)."""


class ScalarEvaluator:
    """`oprf.mask(scalar).mask(oprf.data(point))` for one scalar."""

    def __init__(self, scalar: bytes) -> None:
        if not is_usable_scalar(scalar):
            raise UnusableScalarError("a scalar is 32 bytes and not all zeros")
        self._mask = oprf.mask(scalar)

    def evaluate(self, point: bytes) -> bytes | None:
        """The 32 bytes of `scalar * point`, or None when the library refuses the point."""
        try:
            return bytes(self._mask.mask(oprf.data(point)))
        except (RuntimeError, ValueError):
            return None

    def __repr__(self) -> str:
        """Never show the scalar."""
        return "ScalarEvaluator()"


class ScalarKeyring:
    """The evaluators this process holds, by key version."""

    def __init__(self, scalars: Mapping[KeyVersion, bytes]) -> None:
        self._evaluators = {version: ScalarEvaluator(scalar) for version, scalar in scalars.items()}

    def evaluator(self, version: KeyVersion) -> ScalarEvaluator | None:
        """The evaluator of a version this process holds a scalar for, or None."""
        return self._evaluators.get(version)

    def __repr__(self) -> str:
        """The versions held, never a scalar."""
        return f"ScalarKeyring(versions={sorted(v.number for v in self._evaluators)})"
