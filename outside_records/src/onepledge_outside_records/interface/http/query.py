"""Query string validation of outside records (SPEC_DECISIONS 22.1, 22.7, 23 a).

A route declares its parameters. An undeclared one is reported once as `query`
UNKNOWN_PARAMETER and never named; a repeated one is DUPLICATE. An integer is 1 to 18
ASCII digits inside its bounds, so nothing unvalidated reaches a SQL parameter.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from datetime import date

from onepledge_outside_records.domain.errors import ErrorDetail, ValidationFailedError

MAX_OFFSET = 1_000_000
_UNSIGNED = re.compile(r"[0-9]{1,18}", re.ASCII)
_NEGATIVE = re.compile(r"-[0-9]{1,18}", re.ASCII)
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)


class QueryParameters:
    """The query string of one request, checked against the parameters its route declares."""

    def __init__(self, items: Sequence[tuple[str, str]], declared: Collection[str]) -> None:
        self._values: dict[str, str] = {}
        self.details: list[ErrorDetail] = []
        unknown = False
        for name, value in items:
            if name not in declared:
                unknown = True
            elif name in self._values:
                self.add(name, "DUPLICATE")
            else:
                self._values[name] = value
        if unknown:
            self.details.insert(0, ErrorDetail("query", "UNKNOWN_PARAMETER"))

    def add(self, name: str, issue: str) -> None:
        """Record one problem with a declared parameter (each pair once)."""
        detail = ErrorDetail(name, issue)
        if detail not in self.details:
            self.details.append(detail)

    def text(self, name: str) -> str | None:
        """The raw value of a declared parameter, or None when absent."""
        return self._values.get(name)

    def integer(self, name: str, default: int, minimum: int, maximum: int) -> int:
        """A bounded integer: INVALID_INTEGER unless ASCII digits, OUT_OF_RANGE outside the bounds."""
        text = self._values.get(name)
        if text is None:
            return default
        if _NEGATIVE.fullmatch(text):
            self.add(name, "OUT_OF_RANGE")
            return default
        if not _UNSIGNED.fullmatch(text):
            self.add(name, "INVALID_INTEGER")
            return default
        number = int(text)
        if not minimum <= number <= maximum:
            self.add(name, "OUT_OF_RANGE")
            return default
        return number

    def date(self, name: str) -> date | None:
        """An ISO `YYYY-MM-DD` date in ASCII digits, or INVALID_DATE."""
        text = self._values.get(name)
        if text is None:
            return None
        if _ISO_DATE.fullmatch(text):
            try:
                return date.fromisoformat(text)
            except ValueError:
                self.add(name, "INVALID_DATE")
                return None
        self.add(name, "INVALID_DATE")
        return None

    def finish(self) -> None:
        """Raise one validation error naming every problem found."""
        if self.details:
            raise ValidationFailedError(tuple(self.details))
