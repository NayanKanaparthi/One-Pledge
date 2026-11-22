"""Reading the answers of the shared services: strict shapes, no float anywhere, named errors otherwise.

Every body is parsed twice from the same bytes: once by a pydantic model in strict JSON
mode (wrong types, missing fields and out of range values are refused; unknown fields
are ignored, for additive evolution), and once as plain JSON with floats refused, which
is the form a signature is checked over. A status that is not a success is turned into
the dependency's named error: transport failures, 429 and 5xx are transient; 401, 403
and every other 4xx are refusals; anything outside the contract is a protocol error.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import DependencyRefusal
from onepledge_bank_agent.domain.errors import PipelineStageError

RATE_LIMITED = 429
CLIENT_ERRORS = range(400, 500)
SERVER_ERRORS = range(500, 600)
REFUSALS: Mapping[int, DependencyRefusal] = MappingProxyType(
    {401: DependencyRefusal.UNAUTHENTICATED, 403: DependencyRefusal.FORBIDDEN}
)


class Strict(BaseModel):
    """A wire model: strict types, unknown fields ignored, immutable."""

    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)


class ErrorCode(Strict):
    """The `code` of an `ErrorResponse`."""

    code: str


class ErrorBody(Strict):
    """An `ErrorResponse` (only the code is read)."""

    error: ErrorCode


@dataclass(frozen=True)
class DependencyErrors:
    """How one dependency's failures are named."""

    unavailable: Callable[[int | None], PipelineStageError]
    rejected: Callable[[int, DependencyRefusal], PipelineStageError]
    protocol: Callable[[int | None], PipelineStageError]


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


def failure(response: httpx.Response | None, errors: DependencyErrors) -> PipelineStageError:
    """The named error of an answer that is not the expected success."""
    if response is None:
        return errors.unavailable(None)
    status = response.status_code
    if status == RATE_LIMITED or status in SERVER_ERRORS:
        return errors.unavailable(status)
    if status in CLIENT_ERRORS:
        return errors.rejected(status, REFUSALS.get(status, DependencyRefusal.REQUEST_REFUSED))
    return errors.protocol(status)


def parse[M: BaseModel](
    model: type[M], response: httpx.Response, errors: DependencyErrors
) -> tuple[M, Mapping[str, JsonValue]]:
    """The validated model and the plain JSON object of a body; a protocol error when either fails."""
    try:
        parsed = model.model_validate_json(response.content)
        document = plain_json(response)
    except (ValidationError, ValueError):
        raise errors.protocol(response.status_code) from None
    if not isinstance(document, dict):
        raise errors.protocol(response.status_code)
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
