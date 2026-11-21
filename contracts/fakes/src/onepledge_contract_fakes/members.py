"""Member authentication by API key hash (TEST DOUBLES, SPEC 4).

A double stores only the SHA-256 of each key. It compares a presented key's hash with
every configured hash using `hmac.compare_digest` and does not stop at the first match,
so the time taken does not depend on which member, if any, holds the key (SPEC 4).
"""

from __future__ import annotations

import hashlib
import hmac

from onepledge_contract_fakes.errors import UnauthenticatedError
from onepledge_contract_fakes.settings import MemberCredential


def api_key_sha256(api_key: str) -> str:
    """SHA-256 hex of an API key as it is stored."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


class MemberDirectory:
    """The configured members of one double and their key hashes."""

    def __init__(self, members: tuple[MemberCredential, ...]) -> None:
        self._members = members

    @property
    def member_ids(self) -> tuple[str, ...]:
        """Member ids in configuration order."""
        return tuple(m.member_id for m in self._members)

    def identify(self, api_key: str | None) -> str | None:
        """Return the member id of a key, or None when the key is missing or unknown."""
        if not api_key:
            return None
        presented = api_key_sha256(api_key)
        matched: str | None = None
        for member in self._members:
            if hmac.compare_digest(presented, member.api_key_sha256):
                matched = member.member_id
        return matched

    def authenticate(self, api_key: str | None) -> str:
        """Return the member id of a key, or raise UnauthenticatedError."""
        member_id = self.identify(api_key)
        if member_id is None:
            raise UnauthenticatedError
        return member_id


def key_matches(api_key: str | None, expected_sha256: str) -> bool:
    """Whether a presented key hashes to an expected SHA-256, in constant time."""
    if not api_key:
        return False
    return hmac.compare_digest(api_key_sha256(api_key), expected_sha256)
