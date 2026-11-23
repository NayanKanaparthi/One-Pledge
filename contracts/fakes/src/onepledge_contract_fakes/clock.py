"""Clock and identifier sources of the test doubles (TEST DOUBLES).

Both are injected so tests can fix time and identifiers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

from onepledge_contract_fakes.bounds import timestamp_in_bounds
from onepledge_contract_fakes.errors import InvariantViolationError


class Clock(Protocol):
    """Source of the current time, timezone aware UTC."""

    def now(self) -> datetime:
        """The current time."""
        ...


class IdSource(Protocol):
    """Source of new random UUIDs."""

    def new_id(self) -> str:
        """A new lower case UUID version 4."""
        ...


class SystemClock:
    """The real UTC clock."""

    def now(self) -> datetime:
        """The current time in UTC."""
        return datetime.now(UTC)


class RandomIds:
    """Random UUID version 4 identifiers."""

    def new_id(self) -> str:
        """A new random UUID."""
        return str(uuid.uuid4())


def rfc3339(moment: datetime) -> str:
    """Render a UTC time as RFC 3339 with a `Z` suffix and second precision.

    A double never answers a timestamp outside the bounds of SPEC_DECISIONS 100, or one with
    no offset: either would be a defect of the double or of its clock, not of the caller.
    """
    if not timestamp_in_bounds(moment):
        raise InvariantViolationError
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
