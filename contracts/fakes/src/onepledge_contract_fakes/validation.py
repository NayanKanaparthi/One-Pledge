"""Input validation shared by both test doubles: the issue vocabulary, query parameters and JSON bodies.

The rules are those of SPEC_DECISIONS 22 (finding 7) and 23 (a, b) for every layer 1
service. An error detail names a field from a closed set (a contract property or
parameter name, or `body` / `query`) and an issue from `Issue`; it never carries a
value or a name the client invented.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from enum import StrEnum, auto

from onepledge_contract_fakes.errors import ErrorDetail, ValidationFailedError

MAX_OFFSET = 1_000_000
_UNSIGNED = re.compile(r"[0-9]{1,18}", re.ASCII)
_NEGATIVE = re.compile(r"-[0-9]{1,18}", re.ASCII)


class Issue(StrEnum):
    """The one issue vocabulary of every layer 1 service, plus the codes a contract names."""

    @staticmethod
    def _generate_next_value_(name: str, start: int, count: int, last_values: list[str]) -> str:
        """Each member's value is its own name."""
        return name

    REQUIRED = auto()
    INVALID_TYPE = auto()
    INVALID_INTEGER = auto()
    OUT_OF_RANGE = auto()
    PATTERN_MISMATCH = auto()
    INVALID_VALUE = auto()
    INVALID_DATE = auto()
    TOO_LONG = auto()
    TOO_SHORT = auto()
    TOO_MANY_ITEMS = auto()
    TOO_FEW_ITEMS = auto()
    DUPLICATE = auto()
    UNKNOWN_FIELD = auto()
    UNKNOWN_PARAMETER = auto()
    NOT_A_VALID_POINT = auto()
    DUPLICATE_ASSET_TOKEN = auto()


def parse_integer_text(text: str, minimum: int, maximum: int) -> int | Issue:
    """An integer written as 1 to 18 ASCII digits and inside its bounds, or the issue.

    A sign, a space, a decimal point, an exponent, a non-ASCII digit, an empty value or
    more than 18 digits is INVALID_INTEGER. A well formed negative number is outside
    every bound here, so it is OUT_OF_RANGE, like any other number outside its bounds.
    """
    if _NEGATIVE.fullmatch(text):
        return Issue.OUT_OF_RANGE
    if not _UNSIGNED.fullmatch(text):
        return Issue.INVALID_INTEGER
    number = int(text)
    if not minimum <= number <= maximum:
        return Issue.OUT_OF_RANGE
    return number


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
                self._add(name, Issue.DUPLICATE)
            else:
                self._values[name] = value
        if unknown:
            self.details.insert(0, ErrorDetail("query", Issue.UNKNOWN_PARAMETER))

    def _add(self, name: str, issue: Issue) -> None:
        detail = ErrorDetail(name, issue)
        if detail not in self.details:
            self.details.append(detail)

    def integer(self, name: str, default: int, minimum: int, maximum: int) -> int:
        """A bounded integer parameter (the default when absent)."""
        text = self._values.get(name)
        if text is None:
            return default
        parsed = parse_integer_text(text, minimum, maximum)
        if isinstance(parsed, Issue):
            self._add(name, parsed)
            return default
        return parsed

    def choice(self, name: str, values: Collection[str]) -> str | None:
        """One of an enumeration, or None when absent."""
        text = self._values.get(name)
        if text is not None and text not in values:
            self._add(name, Issue.INVALID_VALUE)
            return None
        return text

    def finish(self) -> None:
        """Raise one validation error naming every problem found."""
        if self.details:
            raise ValidationFailedError(details=tuple(self.details))


class JsonFields:
    """A JSON object body with named fields, checked by type and bounds (used by the test controls)."""

    def __init__(self, document: object, required: Collection[str], optional: Collection[str] = ()) -> None:
        self.details: list[ErrorDetail] = []
        self._document: dict[str, object] = document if isinstance(document, dict) else {}
        if not isinstance(document, dict):
            self.details.append(ErrorDetail("body", Issue.INVALID_TYPE))
            return
        if set(document) - set(required) - set(optional):
            self.details.append(ErrorDetail("body", Issue.UNKNOWN_FIELD))
        self.details.extend(ErrorDetail(name, Issue.REQUIRED) for name in required if name not in document)

    def present(self, name: str) -> bool:
        """Whether the body has this field."""
        return name in self._document

    def integer(self, name: str, minimum: int, maximum: int) -> int | None:
        """A JSON integer (not a float, not a boolean) inside its bounds."""
        value = self._document.get(name)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            self.details.append(ErrorDetail(name, Issue.INVALID_TYPE))
            return None
        if not minimum <= value <= maximum:
            self.details.append(ErrorDetail(name, Issue.OUT_OF_RANGE))
            return None
        return value

    def choice(self, name: str, values: Collection[str]) -> str | None:
        """A JSON string from an enumeration."""
        value = self._document.get(name)
        if value is None:
            return None
        if not isinstance(value, str):
            self.details.append(ErrorDetail(name, Issue.INVALID_TYPE))
            return None
        if value not in values:
            self.details.append(ErrorDetail(name, Issue.INVALID_VALUE))
            return None
        return value

    def string(self, name: str) -> str | None:
        """A JSON string."""
        value = self._document.get(name)
        if value is None:
            return None
        if not isinstance(value, str):
            self.details.append(ErrorDetail(name, Issue.INVALID_TYPE))
            return None
        return value

    def reject(self, name: str, issue: Issue) -> None:
        """Record a problem the caller found with a field."""
        self.details.append(ErrorDetail(name, issue))

    def finish(self) -> None:
        """Raise one validation error naming every problem found."""
        if self.details:
            raise ValidationFailedError(details=tuple(self.details))
