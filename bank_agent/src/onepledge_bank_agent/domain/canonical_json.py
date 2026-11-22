"""Canonical JSON, the only byte form ever hashed or signed (SPEC 4).

`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
allow_nan=False).encode("utf-8")`. Floats are refused, so money can never travel
as a binary fraction.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from onepledge_bank_agent.domain.errors import NonCanonicalValueError

type JsonValue = str | int | bool | Sequence[JsonValue] | Mapping[str, JsonValue] | None

ZERO_HASH = "0" * 64


def _refuse_floats(value: object) -> None:
    if isinstance(value, float):
        raise NonCanonicalValueError("canonical_json", "INVALID_TYPE")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise NonCanonicalValueError("canonical_json", "INVALID_TYPE")
            _refuse_floats(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _refuse_floats(item)


def canonical_json(value: object) -> bytes:
    """The canonical UTF-8 bytes of a JSON value; a float raises `NonCanonicalValueError` (a `TypeError`)."""
    _refuse_floats(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Lower case hex SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: object) -> str:
    """SHA-256 of the canonical JSON of a value."""
    return sha256_hex(canonical_json(value))
