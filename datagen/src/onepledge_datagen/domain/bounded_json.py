"""Reading the JSON files of a data set with bounds, so no file can exhaust the parser.

The same bounds as every request body of layer 1 (SPEC_DECISIONS 38): valid UTF-8, nesting
at most 64 levels, no number literal longer than 30 characters, no duplicate key, no `NaN`
or `Infinity`. A file outside them is malformed, named in one sentence with what is wrong.
"""

from __future__ import annotations

import json

from onepledge_datagen.domain.errors import DataSetUnreadableError

MAX_DEPTH = 64
MAX_NUMBER_LENGTH = 30
_NUMBER_CHARACTERS = frozenset("0123456789+-.eE")
_OPENING, _CLOSING = frozenset("[{"), frozenset("]}")


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


def _within_bounds(text: str) -> bool:
    """Depth and number length on the raw text, strings skipped."""
    depth = number_length = 0
    index, end = 0, len(text)
    while index < end:
        char = text[index]
        if char == '"':
            index = _string_end(text, index + 1)
            number_length = 0
            continue
        number_length = number_length + 1 if char in _NUMBER_CHARACTERS else 0
        depth += (char in _OPENING) - (char in _CLOSING)
        if number_length > MAX_NUMBER_LENGTH or depth > MAX_DEPTH:
            return False
        index += 1
    return True


def parse_bounded_json(data: bytes, name: str) -> object:
    """The JSON document of a data set file, or DataSetUnreadableError naming the file and the problem."""
    refused = DataSetUnreadableError(f"{name} repeats a key or holds NaN or Infinity.")

    def without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise refused
            document[key] = value
        return document

    def no_constants(constant: str) -> object:
        raise refused

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise DataSetUnreadableError(f"{name} is not UTF-8 text.") from None
    if not _within_bounds(text):
        raise DataSetUnreadableError(f"{name} nests too deeply or holds a number literal that is too long.")
    try:
        return json.loads(text, object_pairs_hook=without_duplicates, parse_constant=no_constants)
    except json.JSONDecodeError as error:
        where = f"line {error.lineno}, column {error.colno}"
        raise DataSetUnreadableError(f"{name} is not valid JSON at {where}.") from None
    except (ValueError, RecursionError):
        raise DataSetUnreadableError(f"{name} is not valid JSON.") from None
