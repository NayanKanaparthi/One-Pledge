"""Canonical spellings of identifiers and encodings read from outside the bank (SPEC_DECISIONS 75).

A UUID is accepted only as 36 characters of lower case hexadecimal with hyphens in the
8-4-4-4-12 positions: braces, `urn:uuid:`, upper case or the 32 character form are
refused, never rewritten. A base64 value is accepted only when decoding it and encoding
the bytes again gives the same text, so a value with non-zero padding bits, which
decodes to the same bytes as another spelling, is refused. Every answer, message and
setting the bank reads goes through these two functions.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid

CANONICAL_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def canonical_uuid(text: str) -> uuid.UUID | None:
    """The UUID of its one canonical spelling, or None for any other text."""
    if CANONICAL_UUID.fullmatch(text) is None:
        return None
    return uuid.UUID(text)


def canonical_base64(text: str) -> bytes | None:
    """The bytes of standard base64 that re-encodes to exactly `text`, or None."""
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if base64.b64encode(raw).decode("ascii") == text else None


_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")


def holds_unstorable_text(value: object) -> bool:
    """True when a string or key anywhere in a JSON value holds text the bank cannot store.

    PostgreSQL refuses U+0000 in `text` and `jsonb`, and no UTF-8 text can carry a UTF-16
    surrogate code point (U+D800 to U+DFFF), which JSON can spell as an escape. Both are
    valid JSON, so everything read from a shared service is checked for them before any of
    it is stored.
    """
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if _UNSTORABLE.search(item) is not None:
                return True
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list | tuple):
            pending.extend(item)
    return False
