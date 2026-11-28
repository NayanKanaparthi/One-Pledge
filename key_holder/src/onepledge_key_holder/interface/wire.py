"""The wire rules of `EvaluateRequest` and the error envelope (`contracts/key_holder.yaml`).

A problem is a field name and an issue code. No message, detail or log line ever repeats a
submitted value. The issue codes and the order of the checks are the layer 1 double's
(decision 11): an unknown query parameter or body field is reported first and alone, then
every field problem together.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from onepledge_key_holder.domain.elements import MAX_ELEMENTS
from onepledge_key_holder.domain.evaluation import EvaluationRequest
from onepledge_key_holder.domain.identity import MAX_KEY_VERSION, KeyVersion

MAX_BODY_BYTES = 1_048_576
JSON_MEDIA_TYPE = "application/json"
ELEMENT_LENGTH = 44
_ELEMENT = re.compile(r"[A-Za-z0-9+/]{43}=")
_CANONICAL_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_FIELDS = frozenset({"request_id", "key_version", "elements"})

# The fixed sentence of each code. None is built from request data.
MESSAGES: Mapping[str, str] = MappingProxyType(
    {
        "UNAUTHENTICATED": "A valid API key is required.",
        "VALIDATION_ERROR": "The request is not valid. See details.",
        "NOT_FOUND": "The requested resource does not exist.",
        "METHOD_NOT_ALLOWED": "This method is not allowed on this resource.",
        "INVALID_ELEMENT": "One or more elements are not valid group elements.",
        "KEY_VERSION_UNAVAILABLE": "The requested key version is not available on this key holder.",
        "PAYLOAD_TOO_LARGE": "The request body is too large.",
        "RATE_LIMITED": "The daily limit for this caller is used up. Retry after the time in Retry-After.",
        "SERVICE_UNAVAILABLE": "The service is temporarily unavailable. Try again.",
        "INTERNAL_ERROR": "An unexpected error occurred. Quote the request id when reporting it.",
    }
)


@dataclass(frozen=True)
class Detail:
    """`ErrorDetail`: a field name or pointer and an issue code. Never a value."""

    field: str
    issue: str


class ApiError(Exception):
    """An answer in the contract's error envelope: a status, a code, and optional details."""

    def __init__(
        self, status: int, code: str, details: Sequence[Detail] = (), retry_after_seconds: int | None = None
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.details = tuple(details)
        self.retry_after_seconds = retry_after_seconds


def validation_error(*details: Detail) -> ApiError:
    """422 VALIDATION_ERROR with these details."""
    return ApiError(422, "VALIDATION_ERROR", details)


def is_canonical_uuid(text: str) -> bool:
    """36 lower case hexadecimal characters with hyphens in the 8-4-4-4-12 positions."""
    return _CANONICAL_UUID.fullmatch(text) is not None


class _RefusedJsonError(ValueError):
    """An object repeats a key, or the text uses NaN or Infinity."""


def _object_without_repeats(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise _RefusedJsonError
        document[key] = value
    return document


def _refuse_constant(name: str) -> object:
    raise _RefusedJsonError(name)


def parse_json_object(body: bytes, content_type: str | None) -> dict[str, object]:
    """The body as one JSON object: the Content-Type first, then the parse."""
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type != JSON_MEDIA_TYPE:
        raise validation_error(Detail("Content-Type", "INVALID_VALUE"))
    try:
        document = json.loads(
            body.decode("utf-8"), object_pairs_hook=_object_without_repeats, parse_constant=_refuse_constant
        )
    except (ValueError, RecursionError):
        raise validation_error(Detail("body", "INVALID_JSON")) from None
    if not isinstance(document, dict):
        raise validation_error(Detail("body", "INVALID_JSON"))
    return document


def _request_id_problem(document: Mapping[str, object]) -> str | None:
    if "request_id" not in document:
        return "REQUIRED"
    value = document["request_id"]
    if not isinstance(value, str):
        return "INVALID_TYPE"
    return None if is_canonical_uuid(value) else "PATTERN_MISMATCH"


def _key_version_problem(document: Mapping[str, object]) -> str | None:
    value = document.get("key_version")
    if value is None:
        return None
    if type(value) is not int:
        return "INVALID_TYPE"
    return None if 1 <= value <= MAX_KEY_VERSION else "OUT_OF_RANGE"


def _element_problem(value: object) -> str | None:
    if not isinstance(value, str):
        return "INVALID_TYPE"
    if len(value) < ELEMENT_LENGTH:
        return "TOO_SHORT"
    if len(value) > ELEMENT_LENGTH:
        return "TOO_LONG"
    return None if _ELEMENT.fullmatch(value) else "PATTERN_MISMATCH"


def _elements_problems(document: Mapping[str, object]) -> list[Detail]:
    if "elements" not in document:
        return [Detail("elements", "REQUIRED")]
    value = document["elements"]
    if not isinstance(value, list):
        return [Detail("elements", "INVALID_TYPE")]
    if not value:
        return [Detail("elements", "TOO_FEW_ITEMS")]
    if len(value) > MAX_ELEMENTS:
        return [Detail("elements", "TOO_MANY_ITEMS")]
    problems = ((index, _element_problem(item)) for index, item in enumerate(value))
    return [Detail(f"elements[{index}]", issue) for index, issue in problems if issue is not None]


def parse_evaluate_request(document: Mapping[str, object], has_query: bool) -> EvaluationRequest:
    """A well formed `EvaluateRequest`, or 422 VALIDATION_ERROR naming fields and issues only."""
    unknown = [Detail("query", "UNKNOWN_PARAMETER")] if has_query else []
    if set(document) - _FIELDS:
        unknown.append(Detail("body", "UNKNOWN_FIELD"))
    if unknown:
        raise validation_error(*unknown)
    details: list[Detail] = []
    for field, problem in (
        ("request_id", _request_id_problem(document)),
        ("key_version", _key_version_problem(document)),
    ):
        if problem is not None:
            details.append(Detail(field, problem))
    details.extend(_elements_problems(document))
    if details:
        raise validation_error(*details)
    request_id, key_version, elements = (
        document["request_id"],
        document.get("key_version"),
        document["elements"],
    )
    if not isinstance(request_id, str) or not isinstance(elements, list):
        raise validation_error(Detail("body", "INVALID_TYPE"))
    version = KeyVersion(key_version) if isinstance(key_version, int) else None
    return EvaluationRequest(request_id, version, tuple(str(item) for item in elements))
