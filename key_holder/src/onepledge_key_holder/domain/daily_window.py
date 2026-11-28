"""The window of the daily element cap: the UTC calendar day (SPEC 4.1 `rate_limits`)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta


def window_start(now: datetime) -> datetime:
    """Midnight UTC of the day `now` falls in."""
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def seconds_until_reset(now: datetime) -> int:
    """Whole seconds until the next UTC midnight, at least 1 (`Retry-After`)."""
    remaining = window_start(now) + timedelta(days=1) - now.astimezone(UTC)
    return max(1, math.floor(remaining.total_seconds()))
