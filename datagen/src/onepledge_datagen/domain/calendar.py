"""Date arithmetic and the date spellings lenders use."""

from __future__ import annotations

import calendar
from datetime import date

MONTH_ABBREVIATIONS: tuple[str, ...] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip
MONTHS_PER_YEAR = 12


def add_months(day: date, months: int) -> date:
    """The same day of the month `months` later, clamped to the month's last day."""
    index = day.month - 1 + months
    year = day.year + index // MONTHS_PER_YEAR
    month = index % MONTHS_PER_YEAR + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def whole_months_between(start: date, end: date) -> int:
    """Whole calendar months elapsed from `start` to `end` (0 when end is before start)."""
    months = (end.year - start.year) * MONTHS_PER_YEAR + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def us_date(day: date) -> str:
    """`MM/DD/YYYY`."""
    return f"{day.month:02d}/{day.day:02d}/{day.year:04d}"


def iso_date(day: date) -> str:
    """`YYYY-MM-DD`."""
    return day.isoformat()


def day_month_year(day: date) -> str:
    """`DD-Mon-YYYY` with an English month abbreviation, for example `14-Aug-2026`."""
    return f"{day.day:02d}-{MONTH_ABBREVIATIONS[day.month - 1]}-{day.year:04d}"
