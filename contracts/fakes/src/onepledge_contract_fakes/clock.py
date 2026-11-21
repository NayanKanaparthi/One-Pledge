"""Clock and identifier sources of the test doubles (TEST DOUBLES).

Both are injected so tests can fix time and identifiers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol


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
    """Render a UTC time as RFC 3339 with a `Z` suffix and second precision."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
