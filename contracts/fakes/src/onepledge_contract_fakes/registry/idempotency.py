"""Stored submission responses keyed by member and idempotency key (TEST DOUBLE).

A repeat with the same key and the same body gets the stored bytes back; a repeat
with a different body is refused. Bodies are compared by the SHA-256 of their
canonical JSON.
"""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_contract_fakes.errors import IdempotencyKeyReusedError


@dataclass(frozen=True)
class StoredResponse:
    """The exact response bytes of the first request with a key."""

    body_sha256: str
    response: bytes
    submission_id: str


class IdempotencyStore:
    """First response per (member, idempotency key)."""

    def __init__(self) -> None:
        self._stored: dict[tuple[str, str], StoredResponse] = {}

    def replay(self, member_id: str, key: str, body_sha256: str) -> bytes | None:
        """The stored bytes for a repeat, None for a new key, or IdempotencyKeyReusedError."""
        stored = self._stored.get((member_id, key))
        if stored is None:
            return None
        if stored.body_sha256 != body_sha256:
            raise IdempotencyKeyReusedError
        return stored.response

    def store(self, member_id: str, key: str, stored: StoredResponse) -> None:
        """Keep the response of the first request with a key."""
        self._stored[(member_id, key)] = stored
