"""The round-trip bounds of SPEC_DECISIONS 100 for what the doubles store or return (TEST DOUBLES).

A value may be kept or answered only inside what the contract allows, what a PostgreSQL
column of its kind holds and what the Python type reading it holds: a timestamp from
2000-01-01T00:00:00Z up to but not including 2100-01-01T00:00:00Z, a date from 2000-01-01 to
2099-12-31, an integer of an `int` column at most 2,147,483,647 and of a `bigint` column at most
9,223,372,036,854,775,807, and text of at most 255 characters where the contract gives no
bound, valid Unicode with no control character.
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, date, datetime

TIMESTAMP_START = datetime(2000, 1, 1, tzinfo=UTC)
TIMESTAMP_END = datetime(2100, 1, 1, tzinfo=UTC)
DATE_FIRST, DATE_LAST = date(2000, 1, 1), date(2099, 12, 31)
INT_MAX = 2**31 - 1
BIGINT_MAX = 2**63 - 1
MAX_TEXT = 255


def timestamp_in_bounds(moment: datetime) -> bool:
    """Whether an aware moment, converted to UTC, lies in [2000-01-01, 2100-01-01)."""
    return moment.tzinfo is not None and TIMESTAMP_START <= moment.astimezone(UTC) < TIMESTAMP_END


def text_in_bounds(text: str, max_length: int = MAX_TEXT) -> bool:
    """Valid Unicode, no control character (U+0000 included) and at most `max_length` characters."""
    return len(text) <= max_length and not any(unicodedata.category(char) in ("Cc", "Cs") for char in text)
