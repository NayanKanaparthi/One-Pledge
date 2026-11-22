"""Reading the answers of the shared services: strict shapes, no float anywhere, named errors otherwise.

Every body is parsed twice from the same bytes: once by a pydantic model in strict JSON
mode (wrong types, missing fields and out of range values are refused; unknown fields
are ignored, for additive evolution), and once as plain JSON with floats refused, which
is the form a signature is checked over. A body that does not parse is MALFORMED_RESPONSE
(SPEC_DECISIONS 49); a status that is not a success is classified in `classification.py`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.infrastructure.http.classification import DependencyErrors


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
    """The body as plain JSON, refusing floats; raises `ValueError` when it is not JSON or holds a float."""
    return json.loads(response.content, parse_float=_refuse_float)


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
        parsed = model.model_validate_json(response.content)
        document = plain_json(response)
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
