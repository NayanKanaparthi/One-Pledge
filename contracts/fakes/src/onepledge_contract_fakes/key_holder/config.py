"""Configuration of the key holder double (TEST DOUBLE)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from onepledge_contract_fakes.settings import MemberCredential


@dataclass(frozen=True)
class KeyHolderConfig:
    """Everything the in-memory key holder double needs. Scalars are raw 32 byte values."""

    holder_id: str
    scalars: Mapping[int, bytes]
    active_version: int
    members: tuple[MemberCredential, ...]
    daily_element_cap: int = 1_000_000
    test_control_key: str | None = None

    @property
    def test_controls(self) -> bool:
        """Whether the `/_test/` endpoints are mounted."""
        return self.test_control_key is not None

    def __repr__(self) -> str:
        """Never show the scalars or the test control key."""
        versions = sorted(self.scalars)
        return (
            f"KeyHolderConfig(holder_id={self.holder_id!r}, versions={versions}, "
            f"active_version={self.active_version}, members={len(self.members)}, "
            f"test_controls={self.test_controls})"
        )
