"""Controllable clock and identifier generators for tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta


class ManualClock:
    """A clock that only moves when told to."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        """The current test time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self._now += timedelta(seconds=seconds)


class CountingIds:
    """Deterministic UUID version 4 values, counting up."""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def new_id(self) -> uuid.UUID:
        """The next identifier."""
        value = uuid.UUID(int=self._next, version=4)
        self._next += 1
        return value
