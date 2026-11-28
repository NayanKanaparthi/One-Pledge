"""The real clock and the real id source."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


class SystemClock:
    """The current time in UTC."""

    def now(self) -> datetime:
        """The current time, timezone aware."""
        return datetime.now(UTC)


class RandomIds:
    """Random UUID version 4 identifiers."""

    def new_id(self) -> uuid.UUID:
        """A new random UUID."""
        return uuid.uuid4()
