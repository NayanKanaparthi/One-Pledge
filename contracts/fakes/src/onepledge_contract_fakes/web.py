"""HTTP plumbing shared by both doubles (TEST DOUBLES).

Request ids, body size limits, the access log record, the error envelope of the
contracts, and validation that never echoes a submitted value.
"""

from __future__ import annotations

import hmac
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from http import HTTPStatus
from types import MappingProxyType
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError

from onepledge_contract_fakes.errors import (
    DomainError,
    ErrorDetail,
    MethodNotAllowedError,
    PayloadTooLargeError,
    ResourceNotFoundError,
    UnauthenticatedError,
    ValidationFailedError,
)
from onepledge_contract_fakes.logging_setup import LogEvent, get_logger

REQUEST_ID_HEADER = "X-Request-ID"
TEST_CONTROL_HEADER = "X-Test-Control-Key"
MAX_BODY_BYTES = 1_048_576
_HTTP_NOT_FOUND = 404
_HTTP_METHOD_NOT_ALLOWED = 405
_HTTP_SERVER_ERROR = 500
# Routing and framework errors (404, 405, 413 and the like) are handled by status code, so the
# envelope is used without importing the web framework's internals. 500 stays with the
# catch-all handler, which logs the exception.
_HTTP_ERROR_STATUSES = tuple(
    int(status) for status in HTTPStatus if status >= HTTPStatus.BAD_REQUEST and status != _HTTP_SERVER_ERROR
)

type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_ISSUES: Mapping[str, str] = MappingProxyType(
    {
        "missing": "MISSING",
        "extra_forbidden": "UNKNOWN_FIELD",
        "string_pattern_mismatch": "PATTERN_MISMATCH",
        "string_too_short": "TOO_SHORT",
        "string_too_long": "TOO_LONG",
        "too_short": "TOO_SHORT",
        "too_long": "TOO_LONG",
        "uuid_parsing": "INVALID_UUID",
        "uuid_type": "INVALID_UUID",
        "int_parsing": "INVALID_INTEGER",
        "int_type": "INVALID_INTEGER",
        "int_from_float": "INVALID_INTEGER",
        "greater_than_equal": "OUT_OF_RANGE",
        "less_than_equal": "OUT_OF_RANGE",
        "greater_than": "OUT_OF_RANGE",
        "less_than": "OUT_OF_RANGE",
        "literal_error": "INVALID_VALUE",
        "enum": "INVALID_VALUE",
        "string_type": "INVALID_TYPE",
        "list_type": "INVALID_TYPE",
        "dict_type": "INVALID_TYPE",
        "model_type": "INVALID_TYPE",
        "date_from_datetime_parsing": "INVALID_DATE",
        "date_parsing": "INVALID_DATE",
        "value_error": "INVALID_VALUE",
        "json_invalid": "INVALID_JSON",
        "json_type": "INVALID_JSON",
    }
)

_log = get_logger(__name__)


def request_id_of(request: Request) -> str:
    """The request id bound by the middleware."""
    value = request.scope.get("onepledge.request_id")
    return value if isinstance(value, str) else str(uuid.uuid4())


def error_response(error: DomainError, request_id: str) -> JSONResponse:
    """The contract error envelope for a named error."""
    body: dict[str, Any] = {"code": error.code, "message": error.message, "request_id": request_id}
    if error.details:
        body["details"] = [{"field": d.field, "issue": d.issue} for d in error.details]
    headers = {REQUEST_ID_HEADER: request_id}
    if error.retry_after_seconds is not None:
        headers["Retry-After"] = str(error.retry_after_seconds)
    return JSONResponse(status_code=error.http_status, content={"error": body}, headers=headers)


def _field_name(location: tuple[int | str, ...]) -> str:
    parts: list[str] = []
    for part in location:
        if isinstance(part, int):
            parts.append(f"[{part}]")
        elif part not in ("body", "query", "path", "header"):
            parts.append(("." if parts else "") + part)
    return "".join(parts) or "body"


def details_from_pydantic(errors: list[Any]) -> tuple[ErrorDetail, ...]:
    """Field and issue code per pydantic error, with no input or context."""
    details = []
    for error in errors:
        location = tuple(error.get("loc", ()))
        details.append(
            ErrorDetail(field=_field_name(location), issue=_ISSUES.get(error.get("type"), "INVALID"))
        )
    return tuple(details)


def parse_model[M: BaseModel](model: type[M], document: object) -> M:
    """Validate a parsed JSON document against a request model."""
    try:
        return model.model_validate(document)
    except ValidationError as error:
        raise ValidationFailedError(details=details_from_pydantic(list(error.errors()))) from None


def parse_model_json[M: BaseModel](model: type[M], body: bytes) -> M:
    """Validate raw JSON bytes against a strict request model."""
    try:
        return model.model_validate_json(body)
    except ValidationError as error:
        raise ValidationFailedError(details=details_from_pydantic(list(error.errors()))) from None


def parse_json(body: bytes) -> object:
    """Parse a request body as JSON or raise a validation error."""
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationFailedError(details=(ErrorDetail("body", "INVALID_JSON"),)) from None


def check_test_control_key(presented: str | None, expected: str) -> None:
    """Allow a `/_test/` call only with the configured control key."""
    if not presented or not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        raise UnauthenticatedError


def install_error_handlers(app: FastAPI) -> None:
    """Map every error to the contract envelope with a fixed message."""

    async def domain_error(request: Request, exc: Exception) -> Response:
        error = exc if isinstance(exc, DomainError) else DomainError()
        return error_response(error, request_id_of(request))

    async def validation_error(request: Request, exc: Exception) -> Response:
        errors = list(exc.errors()) if isinstance(exc, RequestValidationError) else []
        return error_response(
            ValidationFailedError(details=details_from_pydantic(errors)), request_id_of(request)
        )

    async def http_error(request: Request, exc: Exception) -> Response:
        code = getattr(exc, "status_code", _HTTP_SERVER_ERROR)
        status = code if isinstance(code, int) else _HTTP_SERVER_ERROR
        if status == _HTTP_METHOD_NOT_ALLOWED:
            return error_response(MethodNotAllowedError(), request_id_of(request))
        if status == _HTTP_NOT_FOUND:
            return error_response(ResourceNotFoundError(), request_id_of(request))
        return error_response(DomainError(), request_id_of(request))

    async def unexpected_error(request: Request, exc: Exception) -> Response:
        _log.error(LogEvent.UNHANDLED_EXCEPTION, exc_info=exc)
        return error_response(DomainError(), request_id_of(request))

    app.add_exception_handler(DomainError, domain_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    for status in _HTTP_ERROR_STATUSES:
        app.add_exception_handler(status, http_error)
    app.add_exception_handler(Exception, unexpected_error)


def _request_id_from(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() == b"x-request-id":
            try:
                return str(uuid.UUID(value.decode("latin-1")))
            except ValueError:
                break
    return str(uuid.uuid4())


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            text = value.decode("latin-1")
            return int(text) if text.isdigit() else None
    return None


class RequestContextMiddleware:
    """Binds the request id, limits the body size and writes one access record per request."""

    def __init__(self, app: AsgiApp, max_body_bytes: int = MAX_BODY_BYTES) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Wrap one HTTP request."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request_id = _request_id_from(scope)
        scope["onepledge.request_id"] = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.monotonic()
        status_holder = {"status": _HTTP_SERVER_ERROR}
        declared = _content_length(scope)
        try:
            if declared is not None and declared > self._max_body_bytes:
                await error_response(PayloadTooLargeError(), request_id)(scope, receive, send)
                status_holder["status"] = PayloadTooLargeError.http_status
                return
            await self._app(
                scope, self._limited(receive), self._with_request_id(send, request_id, status_holder)
            )
        finally:
            route = scope.get("route")
            _log.info(
                LogEvent.HTTP_REQUEST,
                method=scope.get("method", ""),
                route=getattr(route, "path", "unmatched"),
                http_status=status_holder["status"],
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            structlog.contextvars.unbind_contextvars("request_id")

    def _limited(self, receive: Receive) -> Receive:
        received = 0
        limit = self._max_body_bytes

        async def wrapped() -> Message:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise PayloadTooLargeError
            return message

        return wrapped

    @staticmethod
    def _with_request_id(send: Send, request_id: str, status_holder: dict[str, int]) -> Send:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        return wrapped
