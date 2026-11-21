"""Per member daily caps on evaluated elements or submitted items (TEST DOUBLES).

The window is the UTC calendar day. A request that would exceed the cap is
refused as a whole with the seconds until the next UTC midnight.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from onepledge_contract_fakes.clock import Clock
from onepledge_contract_fakes.errors import RateLimitedError


class DailyCap:
    """Counts units per member per UTC day against a fixed limit."""

    def __init__(self, limit: int, clock: Clock) -> None:
        self._limit = limit
        self._clock = clock
        self._used: dict[tuple[str, date], int] = {}

    def consume(self, member_id: str, units: int) -> None:
        """Record `units` for the member today, or raise RateLimitedError."""
        now = self._clock.now()
        key = (member_id, now.date())
        used = self._used.get(key, 0)
        if used + units > self._limit:
            raise RateLimitedError(retry_after_seconds=_seconds_to_midnight(now))
        self._used[key] = used + units


def _seconds_to_midnight(now: datetime) -> int:
    midnight = datetime.combine(now.date() + timedelta(days=1), time(0), tzinfo=now.tzinfo)
    return max(1, int((midnight - now).total_seconds()))
