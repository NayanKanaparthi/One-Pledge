"""Active locks of the registry double, indexed by asset code and borrower code (TEST DOUBLE)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from onepledge_contract_fakes.errors import InvariantViolationError


@dataclass(frozen=True)
class Lock:
    """One active lock: one member holds one asset code. `acquired_seq` orders locks by age."""

    lock_id: str
    member_id: str
    asset_token: str
    field_tokens: Mapping[str, str]
    acquired_seq: int
    submission_id: str

    def token(self, kind: str) -> str:
        """The lock's code of one kind; ASSET is the asset code itself."""
        return self.asset_token if kind == "ASSET" else self.field_tokens[kind]


class LockTable:
    """At most one active lock per asset code. Released locks are kept for the dump."""

    def __init__(self) -> None:
        self._by_asset: dict[str, Lock] = {}
        self._by_borrower: dict[str, list[Lock]] = {}
        self._released: list[Lock] = []

    def holder(self, asset_token: str) -> Lock | None:
        """The active lock on an asset code, if any."""
        return self._by_asset.get(asset_token)

    def acquire(self, lock: Lock) -> None:
        """Add a lock. The caller has checked that the asset code is free."""
        if lock.asset_token in self._by_asset:
            raise InvariantViolationError
        self._by_asset[lock.asset_token] = lock
        self._by_borrower.setdefault(lock.field_tokens["BORROWER"], []).append(lock)

    def release(self, lock: Lock) -> None:
        """Remove an active lock."""
        del self._by_asset[lock.asset_token]
        self._by_borrower[lock.field_tokens["BORROWER"]].remove(lock)
        self._released.append(lock)

    def with_borrower(self, borrower_token: str) -> tuple[Lock, ...]:
        """Active locks with a borrower code, oldest first."""
        return tuple(sorted(self._by_borrower.get(borrower_token, []), key=lambda lock: lock.acquired_seq))

    def active(self) -> tuple[Lock, ...]:
        """Every active lock, oldest first."""
        return tuple(sorted(self._by_asset.values(), key=lambda lock: lock.acquired_seq))

    def released(self) -> tuple[Lock, ...]:
        """Every released lock, in release order."""
        return tuple(self._released)

    def count_for(self, member_id: str) -> int:
        """Number of active locks a member holds."""
        return sum(1 for lock in self._by_asset.values() if lock.member_id == member_id)
