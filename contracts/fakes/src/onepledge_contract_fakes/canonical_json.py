"""Canonical JSON used for every hash and signature (TEST DOUBLES, SPEC 4).

The rule is the one written in `contracts/registry.yaml`: keys sorted, separators
`,` and `:`, UTF-8, non-ASCII not escaped, no floats.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

type JsonValue = bool | int | str | Sequence[JsonValue] | Mapping[str, JsonValue] | None


def _reject_floats(value: object) -> None:
    if isinstance(value, float):
        raise TypeError("canonical JSON does not allow floats")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_floats(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_floats(item)


def canonical_json(value: JsonValue) -> bytes:
    """Return the canonical UTF-8 bytes of a JSON value. Raises TypeError on a float."""
    _reject_floats(value)
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Lower case hex SHA-256 of some bytes."""
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: JsonValue) -> str:
    """SHA-256 hex of the canonical JSON of a value."""
    return sha256_hex(canonical_json(value))
