"""Reading the JSON files of a data set with bounds, so no file can exhaust the parser.

The same bounds as every request body of layer 1 (SPEC_DECISIONS 38 and 74): valid UTF-8,
nesting at most 32 levels, no number literal longer than 30 characters, no duplicate key, no
`NaN` or `Infinity`. A file outside them is one problem of that file, named: "FILE: nests too
deeply." for the depth, "FILE: not valid JSON." for anything else the parser refuses.
"""

from __future__ import annotations

import json

from onepledge_datagen.domain.errors import DataSetUnreadableError

MAX_DEPTH = 32
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


TOO_DEEP, TOO_LONG, WITHIN = "TOO_DEEP", "TOO_LONG", "WITHIN"


def _bounds(text: str) -> str:
    """Depth and number length on the raw text, strings skipped: TOO_DEEP, TOO_LONG or WITHIN."""
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
        if depth > MAX_DEPTH:
            return TOO_DEEP
        if number_length > MAX_NUMBER_LENGTH:
            return TOO_LONG
        index += 1
    return WITHIN


def parse_bounded_json(data: bytes, name: str) -> object:
    """The JSON document of a data set file, or DataSetUnreadableError naming the file and the problem."""
    invalid = DataSetUnreadableError(f"{name}: not valid JSON.")

    def without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise invalid
            document[key] = value
        return document

    def no_constants(constant: str) -> object:
        raise invalid

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise DataSetUnreadableError(f"{name}: not UTF-8 text.") from None
    too_deep = DataSetUnreadableError(f"{name}: nests too deeply.")
    bounds = _bounds(text)
    if bounds == TOO_DEEP:
        raise too_deep
    if bounds == TOO_LONG:
        raise invalid
    try:
        return json.loads(text, object_pairs_hook=without_duplicates, parse_constant=no_constants)
    except RecursionError:
        raise too_deep from None
    except ValueError:
        raise invalid from None
