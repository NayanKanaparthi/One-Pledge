"""Reading the answers of the shared services: strict shapes, no float anywhere, named errors otherwise.

Every body is parsed twice from the same bytes: once by a pydantic model in strict JSON
mode (wrong types, missing fields and out of range values are refused; unknown fields
are ignored, for additive evolution), and once as plain JSON with floats refused, which
is the form a signature is checked over. Identifiers and base64 values must be spelled
canonically (`CanonicalUuid`, `CanonicalBase64`, SPEC_DECISIONS 75); any other spelling
fails the model like a wrong type. A body holding text the bank cannot store (U+0000, which
PostgreSQL refuses, or a surrogate code point, which UTF-8 cannot carry) is refused before
any of it is stored. A body that does not parse is MALFORMED_RESPONSE
(SPEC_DECISIONS 49); a status that is not a success is classified in `classification.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Annotated

import httpx
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.storable import storable_json
from onepledge_bank_agent.infrastructure import bounded_json
from onepledge_bank_agent.infrastructure.encodings import canonical_base64, canonical_uuid
from onepledge_bank_agent.infrastructure.http.classification import DependencyErrors


def _uuid(value: object) -> uuid.UUID:
    parsed = canonical_uuid(value) if isinstance(value, str) else None
    if parsed is None:
        raise ValueError("uuid")
    return parsed


def base64_text(value: str) -> str:
    """The value when it is canonical standard base64; a validation error otherwise."""
    if canonical_base64(value) is None:
        raise ValueError("base64")
    return value


type CanonicalUuid = Annotated[uuid.UUID, BeforeValidator(_uuid)]
"""A UUID in its one canonical spelling (36 lower case characters with hyphens)."""


class Strict(BaseModel):
    """A wire model: strict types, unknown fields ignored, immutable."""

    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)


class ErrorCode(Strict):
    """The `code` of an `ErrorResponse`."""

    code: str


class ErrorBody(Strict):
    """An `ErrorResponse` (only the code is read)."""

    error: ErrorCode


def _refuse_float(text: str) -> float:
    raise ValueError(text[:0])


def plain_json(response: httpx.Response) -> object:
    """The body as plain JSON within the bounds of `bounded_json` (no key twice, at most 32 levels deep),
    refusing floats and any value the bank cannot store and read back (SPEC_DECISIONS 100); raises
    `ValueError` when it is not such a document (SPEC_DECISIONS 74)."""
    document = bounded_json.loads(response.content, parse_float=_refuse_float)
    if not storable_json(document):
        raise ValueError("not storable")
    return document


def error_code(response: httpx.Response) -> str | None:
    """The `error.code` of an error body, or None."""
    try:
        return ErrorBody.model_validate_json(response.content).error.code
    except ValidationError:
        return None


def parse[M: BaseModel](
    model: type[M], response: httpx.Response, errors: DependencyErrors
) -> tuple[M, Mapping[str, JsonValue]]:
    """The validated model and the plain JSON object of a body; a protocol error when either fails."""
    try:
        document = plain_json(response)
        parsed = model.model_validate_json(response.content)
    except (ValidationError, ValueError):
        raise errors.malformed(response.status_code) from None
    if not isinstance(document, dict):
        raise errors.malformed(response.status_code)
    return parsed, document


def json_object(value: object) -> Mapping[str, JsonValue]:
    """A JSON object read by `plain_json`, typed as such (an empty object for anything else)."""
    if not isinstance(value, dict):
        return {}
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JsonValue:
    if isinstance(value, dict):
        return json_object(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, str | int | bool):
        return value
    return None
