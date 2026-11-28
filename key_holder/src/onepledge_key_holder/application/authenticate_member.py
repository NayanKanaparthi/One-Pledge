"""`AuthenticateMember`: the member an `X-API-Key` belongs to (SPEC 4.2)."""

from __future__ import annotations

from onepledge_key_holder.application.ports import CredentialDirectory
from onepledge_key_holder.domain.credentials import api_key_sha256
from onepledge_key_holder.domain.errors import UnauthenticatedError


class AuthenticateMember:
    """Looks a presented key up by its SHA-256. The key itself is never stored or compared."""

    def __init__(self, credentials: CredentialDirectory) -> None:
        self._credentials = credentials

    def run(self, api_key: str | None) -> str:
        """The member id, or UnauthenticatedError for a missing or unknown key."""
        if not api_key:
            raise UnauthenticatedError
        member_id = self._credentials.member_for(api_key_sha256(api_key))
        if member_id is None:
            raise UnauthenticatedError
        return member_id
