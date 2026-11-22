"""Bounded JSON request body parsing shared by both test doubles (SPEC_DECISIONS 38).

A body is accepted only as one well formed JSON object: valid UTF-8, nesting at most 32
levels, no number literal longer than 30 characters, no duplicate key in any object, no
`NaN` or `Infinity`. The depth and number checks run on the raw text before any value is
built, so no input can exhaust the parser. Every refusal is `body` INVALID_JSON; a body
sent without `Content-Type: application/json` is `Content-Type` INVALID_VALUE.
"""

from __future__ import annotations

import json

from onepledge_contract_fakes.canonical_json import JsonValue
from onepledge_contract_fakes.errors import ErrorDetail, ValidationFailedError
from onepledge_contract_fakes.validation import Issue

MAX_DEPTH = 32
MAX_NUMBER_LENGTH = 30
JSON_MEDIA_TYPE = "application/json"
_NUMBER_CHARACTERS = frozenset("0123456789+-.eE")
_OPENING, _CLOSING = frozenset("[{"), frozenset("]}")


class MalformedJsonError(ValueError):
    """The text is not one well formed JSON document inside the bounds of SPEC_DECISIONS 38."""


class NestingTooDeepError(MalformedJsonError):
    """The text nests deeper than MAX_DEPTH, found before any value is built."""


class _RefusedJsonError(ValueError):
    """An object repeats a key, or the text uses NaN or Infinity."""


def _invalid() -> ValidationFailedError:
    return ValidationFailedError(details=(ErrorDetail("body", Issue.INVALID_JSON),))


def require_json_content_type(content_type: str | None) -> None:
    """A JSON body must be declared as `application/json` (parameters such as charset allowed)."""
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type != JSON_MEDIA_TYPE:
        raise ValidationFailedError(details=(ErrorDetail("Content-Type", Issue.INVALID_VALUE),))


def _within_bounds(text: str) -> bool:
    """Depth and number length on the raw text, strings skipped; too deep raises NestingTooDeepError."""
    depth = number_length = 0
    index, end = 0, len(text)
    while index < end:
        char = text[index]
        if char == '"':
            index = _string_end(text, index + 1)
            number_length = 0
            continue
        number_length = number_length + 1 if char in _NUMBER_CHARACTERS else 0
        if number_length > MAX_NUMBER_LENGTH:
            return False
        depth += (char in _OPENING) - (char in _CLOSING)
        if depth > MAX_DEPTH:
            raise NestingTooDeepError
        index += 1
    return True


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


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise _RefusedJsonError
        document[key] = value
    return document


def _refuse_constant(name: str) -> object:
    raise _RefusedJsonError(name)


def parse_bounded_json(text: str) -> JsonValue:
    """Any JSON document inside the bounds, or MalformedJsonError (bodies and settings both use it)."""
    if not _within_bounds(text):
        raise MalformedJsonError
    try:
        document: JsonValue = json.loads(
            text, object_pairs_hook=_object_without_duplicates, parse_constant=_refuse_constant
        )
    except RecursionError:
        raise NestingTooDeepError from None
    except ValueError:
        raise MalformedJsonError from None
    return document


def parse_json_object(body: bytes) -> dict[str, JsonValue]:
    """The body as one JSON object, or ValidationFailedError with `body` INVALID_JSON."""
    try:
        document = parse_bounded_json(body.decode("utf-8"))
    except (UnicodeDecodeError, MalformedJsonError):
        raise _invalid() from None
    if not isinstance(document, dict):
        raise _invalid()
    return document


def parse_json_request(body: bytes, content_type: str | None) -> dict[str, JsonValue]:
    """Content type first, then the bounded parse."""
    require_json_content_type(content_type)
    return parse_json_object(body)
