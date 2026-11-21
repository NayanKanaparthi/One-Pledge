"""The system clock (the `Clock` port): timezone aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Reads the wall clock in UTC."""

    def now(self) -> datetime:
        """Now, in UTC."""
        return datetime.now(UTC)
