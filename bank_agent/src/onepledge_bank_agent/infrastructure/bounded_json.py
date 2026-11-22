"""Reading JSON the bank did not write, with bounds (SPEC_DECISIONS 38, 74).

Every answer of a shared service, every registry outbox page and the reference data file
is hostile input. Before any value is built, the nesting depth is measured on the raw
text (strings skipped), so no document can exhaust the parser's recursion; a key given
twice in one object is never resolved silently; `NaN` and `Infinity` are not JSON.
`loads` raises `JsonRefusedError` with the problem (`TOO_DEEP` or `NOT_JSON`) for each of
these and for anything else the parser refuses, so the caller names the file or the
dependency and nothing reaches a last resort handler.

`loads` with `mark_duplicates` keeps an object that holds a key twice as a
`DuplicateKeyObject` (its last value wins) instead of refusing the whole document, so a
caller reading many messages in one document can set aside only the message that holds it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from enum import StrEnum

MAX_DEPTH = 32
MAX_BYTES = 5 * 1024 * 1024
_OPENING, _CLOSING = frozenset("[{"), frozenset("]}")


class JsonProblem(StrEnum):
    """Why a document was refused."""

    TOO_DEEP = "TOO_DEEP"
    NOT_JSON = "NOT_JSON"


class JsonRefusedError(ValueError):
    """The document is not one bounded JSON document; carries the problem, never the content."""

    def __init__(self, problem: JsonProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


class DuplicateKeyObject(dict[str, object]):
    """A JSON object that held at least one key twice; the last value of each key is kept."""


def _string_end(text: str, index: int) -> int:
    """The position after the closing quote of the string whose content starts at `index`."""
    end = len(text)
    while index < end:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        index += 1
        if char == '"':
            break
    return index


def nesting_depth(text: str) -> int:
    """The deepest nesting of arrays and objects in `text`, strings skipped."""
    depth = deepest = index = 0
    end = len(text)
    while index < end:
        char = text[index]
        if char == '"':
            index = _string_end(text, index + 1)
            continue
        if char in _OPENING:
            depth += 1
            deepest = max(deepest, depth)
        elif char in _CLOSING:
            depth -= 1
        index += 1
    return deepest


def _refuse_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise JsonRefusedError(JsonProblem.NOT_JSON)
        document[key] = value
    return document


def _mark_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    return (DuplicateKeyObject if len(set(keys)) != len(keys) else dict)(pairs)


def _no_constant(constant: str) -> object:
    raise JsonRefusedError(JsonProblem.NOT_JSON)


def loads(
    content: bytes | str,
    *,
    max_depth: int = MAX_DEPTH,
    mark_duplicates: bool = False,
    parse_int: Callable[[str], object] | None = None,
    parse_float: Callable[[str], object] | None = None,
) -> object:
    """One JSON document within the bounds, or `JsonRefusedError`."""
    try:
        text = content.decode("utf-8") if isinstance(content, bytes) else content
    except UnicodeDecodeError:
        raise JsonRefusedError(JsonProblem.NOT_JSON) from None
    if nesting_depth(text) > max_depth:
        raise JsonRefusedError(JsonProblem.TOO_DEEP)
    try:
        return json.loads(
            text,
            object_pairs_hook=_mark_duplicates if mark_duplicates else _refuse_duplicates,
            parse_constant=_no_constant,
            parse_int=parse_int,
            parse_float=parse_float,
        )
    except RecursionError:
        raise JsonRefusedError(JsonProblem.TOO_DEEP) from None
    except ValueError as error:
        if isinstance(error, JsonRefusedError):
            raise
        raise JsonRefusedError(JsonProblem.NOT_JSON) from None


def holds_duplicate_key(value: object) -> bool:
    """True when `value` (as read by `loads(..., mark_duplicates=True)`) has an object with a key twice."""
    if isinstance(value, DuplicateKeyObject):
        return True
    if isinstance(value, dict):
        return any(holds_duplicate_key(item) for item in value.values())
    if isinstance(value, list):
        return any(holds_duplicate_key(item) for item in value)
    return False
