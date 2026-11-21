"""Configuration of the registry double (TEST DOUBLE)."""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_contract_fakes.settings import MemberCredential


@dataclass(frozen=True)
class RegistryConfig:
    """Everything the in-memory registry double needs."""

    members: tuple[MemberCredential, ...]
    signing_seed: bytes
    signing_key_id: str
    auditor_key_sha256: str
    key_version: str = "1.1"
    daily_item_cap: int = 1_000_000
    test_control_key: str | None = None

    @property
    def test_controls(self) -> bool:
        """Whether the `/_test/` endpoints are mounted."""
        return self.test_control_key is not None

    def __repr__(self) -> str:
        """Never show the signing seed or the test control key."""
        return (
            f"RegistryConfig(members={len(self.members)}, signing_key_id={self.signing_key_id!r}, "
            f"key_version={self.key_version!r}, daily_item_cap={self.daily_item_cap}, "
            f"test_controls={self.test_controls})"
        )
