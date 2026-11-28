"""The holder identity and the key version, as `contracts/key_holder.yaml` defines them.

`holder_id` is `kh1` or `kh2` (`Health.holder_id`, `EvaluateResponse.holder_id`). A key
version is an integer from 1 (`Health.active_key_version`, `EvaluateResponse.key_version`),
at most the largest PostgreSQL `integer`, the type a version is stored in.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

MAX_KEY_VERSION = 2_147_483_647


class HolderId(StrEnum):
    """Which of the two key holders this process is."""

    KH1 = "kh1"
    KH2 = "kh2"


class InvalidKeyVersionError(ValueError):
    """A key version that is not an integer from 1 to MAX_KEY_VERSION."""


@dataclass(frozen=True, order=True)
class KeyVersion:
    """A key version number: an `int` (never a `bool`) from 1 to MAX_KEY_VERSION."""

    number: int

    def __post_init__(self) -> None:
        """Refuse a value outside the contract's range."""
        if type(self.number) is not int or not 1 <= self.number <= MAX_KEY_VERSION:
            raise InvalidKeyVersionError(f"a key version is an integer from 1 to {MAX_KEY_VERSION:,}")
