"""Reading a request: the body, its bounded parse into a model, the check order, test controls (TEST DOUBLES).

Validation never echoes a submitted value: every problem is a field name and an issue code.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from fastapi import Request
from pydantic import BaseModel, ValidationError

from onepledge_contract_fakes.errors import (
    DomainError,
    ErrorDetail,
    PayloadTooLargeError,
    UnauthenticatedError,
    ValidationFailedError,
)
from onepledge_contract_fakes.validation import Issue, QueryParameters
from onepledge_contract_fakes.web.scope import TEST_CONTROL_HEADER

# pydantic error types mapped to the issue vocabulary of SPEC_DECISIONS 22 (finding 7).
_ISSUES: Mapping[str, Issue] = MappingProxyType(
    {
        "missing": Issue.REQUIRED,
        "extra_forbidden": Issue.UNKNOWN_FIELD,
        "string_pattern_mismatch": Issue.PATTERN_MISMATCH,
        "string_too_short": Issue.TOO_SHORT,
        "string_too_long": Issue.TOO_LONG,
        "too_short": Issue.TOO_FEW_ITEMS,
        "too_long": Issue.TOO_MANY_ITEMS,
        "uuid_parsing": Issue.PATTERN_MISMATCH,
        "greater_than_equal": Issue.OUT_OF_RANGE,
        "less_than_equal": Issue.OUT_OF_RANGE,
        "greater_than": Issue.OUT_OF_RANGE,
        "less_than": Issue.OUT_OF_RANGE,
        "literal_error": Issue.INVALID_VALUE,
        "enum": Issue.INVALID_VALUE,
        "value_error": Issue.INVALID_VALUE,
        "date_from_datetime_parsing": Issue.INVALID_DATE,
        "date_parsing": Issue.INVALID_DATE,
        "json_invalid": Issue.INVALID_JSON,
        "int_from_float": Issue.INVALID_TYPE,
    }
)
# Every "wrong JSON type" error of pydantic ends in `_type` (int_type, string_type, bool_type,
# model_type, uuid_type, json_type and so on).
_TYPE_SUFFIX = "_type"
_ENUM_ERRORS = frozenset({"literal_error", "enum"})
_LOCATION_ROOTS = frozenset({"body", "query", "path", "header"})


def _field_name(location: tuple[int | str, ...]) -> str:
    parts: list[str] = []
    for part in location:
        if isinstance(part, int):
            parts.append(f"[{part}]")
        elif part not in _LOCATION_ROOTS:
            parts.append(("." if parts else "") + part)
    return "".join(parts) or "body"


def _issue(error: Mapping[str, Any]) -> Issue:
    kind = str(error.get("type", ""))
    if kind in _ENUM_ERRORS and not isinstance(error.get("input"), str):
        return Issue.INVALID_TYPE
    if kind in _ISSUES:
        return _ISSUES[kind]
    if kind.endswith(_TYPE_SUFFIX) or kind.endswith("_parsing"):
        return Issue.INVALID_TYPE
    return Issue.INVALID_VALUE


def details_from_pydantic(errors: list[Any]) -> tuple[ErrorDetail, ...]:
    """Field and issue code per pydantic error, with no input or context.

    The field is always a name the request model declares. An unknown key is never named:
    every unknown key, at any depth, is reported once as `body` UNKNOWN_FIELD.
    """
    details: list[ErrorDetail] = []
    for error in errors:
        issue = _issue(error)
        field = "body" if issue == Issue.UNKNOWN_FIELD else _field_name(tuple(error.get("loc", ())))
        detail = ErrorDetail(field=field, issue=issue)
        if detail not in details:
            details.append(detail)
    return tuple(details)


@dataclass(frozen=True)
class BodyRead:
    """A request body read before authentication, with the size or length problem held back.

    The body is read early only so a test double can record it; `accept` raises the held back
    problem, and is called once the caller is authenticated (step 4 of SPEC_DECISIONS 66).
    """

    body: bytes
    problem: DomainError | None

    def accept(self) -> bytes:
        """The body, or the size or length problem found while reading it."""
        if self.problem is not None:
            raise self.problem
        return self.body


async def read_body(request: Request) -> BodyRead:
    """Read the whole body, keeping a size or length problem for `BodyRead.accept`."""
    try:
        return BodyRead(await request.body(), None)
    except (PayloadTooLargeError, ValidationFailedError) as error:
        return BodyRead(b"", error)


def parse_model_json[M: BaseModel](model: type[M], body: bytes) -> M:
    """Validate the JSON bytes of a body already accepted by `json_body` against a strict model."""
    try:
        return model.model_validate_json(body)
    except ValidationError as error:
        raise ValidationFailedError(details=details_from_pydantic(list(error.errors()))) from None


_UNKNOWN = frozenset({Issue.UNKNOWN_FIELD, Issue.UNKNOWN_PARAMETER})


def validate_in_order[T](
    query: QueryParameters, validate_body: Callable[[], T], field_details: Sequence[ErrorDetail] = ()
) -> T:
    """Steps 7 and 8 of SPEC_DECISIONS 66 for a body already past its Content-Type and JSON checks.

    Unknown body fields and unknown query parameters are reported first and alone; only when
    there is none are the field problems of the query, the body and the headers reported, all
    together. Steps 1 to 6 are done before this is called.
    """
    body_details: list[ErrorDetail] = []
    result: T | None = None
    try:
        result = validate_body()
    except ValidationFailedError as error:
        body_details = list(error.details)
    unknown = [*query.unknown_details(), *(d for d in body_details if d.issue in _UNKNOWN)]
    if unknown:
        raise ValidationFailedError(details=tuple(unknown))
    details = [*query.field_details(), *body_details, *field_details]
    if details or result is None:
        raise ValidationFailedError(details=tuple(details))
    return result


def check_test_control(request: Request, expected_key: str, has_body: bool = False) -> QueryParameters:
    """A `/_test/` call: the control key first; none of these routes declares a query parameter.

    Without a body the query is checked at once; with one, `validate_in_order` checks it after
    the body's Content-Type and JSON (SPEC_DECISIONS 66).
    """
    check_test_control_key(header(request, TEST_CONTROL_HEADER), expected_key)
    query = QueryParameters(request.query_params.multi_items(), ())
    if not has_body:
        query.finish()
    return query


# SPEC_DECISIONS 112.8: a repeated credential is 401, any other repeated header 422 DUPLICATE.
AUTHENTICATION_HEADERS = frozenset({"authorization", "x-api-key", "x-test-control-key"})


def repeated(request: Request, name: str) -> bool:
    """Whether the request carries the header more than once."""
    return len(request.headers.getlist(name)) > 1


def header(request: Request, name: str) -> str | None:
    """The one value of a header; a repeated credential is UNAUTHENTICATED, any other DUPLICATE."""
    values = request.headers.getlist(name)
    if len(values) > 1:
        if name.lower() in AUTHENTICATION_HEADERS:
            raise UnauthenticatedError
        raise ValidationFailedError(details=(ErrorDetail(name, Issue.DUPLICATE),))
    return values[0] if values else None


def check_test_control_key(presented: str | None, expected: str) -> None:
    """Allow a `/_test/` call only with the configured control key."""
    if not presented or not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        raise UnauthenticatedError
