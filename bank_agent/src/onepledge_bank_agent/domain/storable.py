"""The round-trip rule: nothing is stored that every reader cannot read back (SPEC_DECISIONS 100).

A value from outside the bank (a registry message or answer, a key holder or outside records
answer, a tape cell, a file) is stored only inside the intersection of what the contract
allows, what the PostgreSQL column holds and what the Python type that reads it holds:

| Kind | Accepted |
|---|---|
| Timestamp | timezone aware; in UTC from 2000-01-01T00:00:00Z up to, not including, 2100-01-01T00:00:00Z |
| Date | 2000-01-01 to 2099-12-31 |
| Integer | `bigint`: -2^63 to 2^63 - 1; `int`: at most 2^31 - 1 |
| Text | no control character (U+0000 to U+001F, U+007F to U+009F), no surrogate, at most 255 characters |
| JSON for `jsonb` | every key and string a storable text, every number an integer in `bigint` range |

`StorableTimestamp` and `StorableDate` are the value objects repositories take; the
conversion to UTC is checked, so an offset that pushes a boundary value outside is refused
(Python itself cannot hold the year 10000 or the year 0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from onepledge_bank_agent.domain.errors import InvalidValueError

EARLIEST_INSTANT = datetime(2000, 1, 1, tzinfo=UTC)
END_INSTANT = datetime(2100, 1, 1, tzinfo=UTC)
EARLIEST_DATE = date(2000, 1, 1)
LATEST_DATE = date(2099, 12, 31)
BIGINT_MIN = -(2**63)
BIGINT_MAX = 2**63 - 1
INT_MAX = 2**31 - 1
MAX_TEXT = 255
OUT_OF_RANGE = "OUT_OF_RANGE"
_UNSTORABLE = re.compile("[\x00-\x1f\x7f-\x9f\ud800-\udfff]")


def utc_instant(value: datetime) -> datetime | None:
    """`value` in UTC when it is aware and inside the stored range, else None."""
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    try:
        instant = value.astimezone(UTC)
    except OverflowError:
        return None
    return instant if EARLIEST_INSTANT <= instant < END_INSTANT else None


@dataclass(frozen=True, order=True)
class StorableTimestamp:
    """An instant from another party that every reader of the database can read back, held in UTC."""

    value: datetime

    def __post_init__(self) -> None:
        """Refuse a naive instant and one outside 2000-01-01T00:00:00Z to 2100-01-01T00:00:00Z."""
        instant = utc_instant(self.value) if isinstance(self.value, datetime) else None
        if instant is None:
            raise InvalidValueError("timestamp", OUT_OF_RANGE)
        object.__setattr__(self, "value", instant)


@dataclass(frozen=True, order=True)
class StorableDate:
    """A calendar date from another party, 2000-01-01 to 2099-12-31."""

    value: date

    def __post_init__(self) -> None:
        """Refuse a date outside the stored range."""
        if isinstance(self.value, datetime) or not EARLIEST_DATE <= self.value <= LATEST_DATE:
            raise InvalidValueError("date", OUT_OF_RANGE)


def storable_text(text: str, max_length: int = MAX_TEXT) -> bool:
    """True when `text` holds no control character and no surrogate and is at most `max_length` long."""
    return len(text) <= max_length and _UNSTORABLE.search(text) is None


def storable_integer(number: int, low: int = BIGINT_MIN, high: int = BIGINT_MAX) -> bool:
    """True when `number` fits the column (`bigint` unless given)."""
    return low <= number <= high


def storable_json(value: object) -> bool:
    """True when every key and string of a JSON value is a storable text and every number an integer in
    `bigint` range; a float, or any other type, is not storable JSON."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if not storable_text(item):
                return False
        elif isinstance(item, bool) or item is None:
            continue
        elif isinstance(item, int):
            if not storable_integer(item):
                return False
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list | tuple):
            pending.extend(item)
        else:
            return False
    return True


def storable_texts(value: object) -> bool:
    """True when every key and string of a JSON value is a storable text; numbers are not looked at."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str) and not storable_text(item):
            return False
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list | tuple):
            pending.extend(item)
    return True
