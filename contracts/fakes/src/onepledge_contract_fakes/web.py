"""HTTP plumbing shared by both doubles (TEST DOUBLES).

Request ids, body size limits, the access log record, the error envelope of the
contracts, and validation that never echoes a submitted value.
"""

from __future__ import annotations

import hmac
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from http import HTTPStatus
from types import MappingProxyType
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

from onepledge_contract_fakes.canonical_json import JsonValue
from onepledge_contract_fakes.errors import (
    DomainError,
    ErrorDetail,
    MethodNotAllowedError,
    PayloadTooLargeError,
    ResourceNotFoundError,
    UnauthenticatedError,
    ValidationFailedError,
)
from onepledge_contract_fakes.json_body import parse_json_request
from onepledge_contract_fakes.logging_setup import LogEvent, get_logger
from onepledge_contract_fakes.validation import MAX_INTEGER, Issue, QueryParameters, parse_integer_text

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
    status.value for status in HTTPStatus if status >= HTTPStatus.BAD_REQUEST and status != _HTTP_SERVER_ERROR
)

type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]

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

_log = get_logger(__name__)


_REQUEST_ID = "onepledge.request_id"


def request_id_of(request: Request) -> str:
    """The request id bound by the middleware."""
    value = request.scope.get(_REQUEST_ID)
    return value if isinstance(value, str) else str(uuid.uuid4())


def error_response(
    error: DomainError, request_id: str, extra_headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """The contract error envelope for a named error."""
    body: dict[str, Any] = {"code": error.code, "message": error.message, "request_id": request_id}
    if error.details:
        body["details"] = [{"field": d.field, "issue": d.issue} for d in error.details]
    headers = {REQUEST_ID_HEADER: request_id, **(extra_headers or {})}
    if error.retry_after_seconds is not None:
        headers["Retry-After"] = str(error.retry_after_seconds)
    return JSONResponse(status_code=error.http_status, content={"error": body}, headers=headers)


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


def parse_model[M: BaseModel](model: type[M], document: object) -> M:
    """Validate a parsed JSON document against a request model."""
    try:
        return model.model_validate(document)
    except ValidationError as error:
        raise ValidationFailedError(details=details_from_pydantic(list(error.errors()))) from None


def parse_request_model[M: BaseModel](
    model: type[M], body: bytes, content_type: str | None
) -> tuple[M, dict[str, JsonValue]]:
    """A JSON request body: content type, bounded parse, then the strict model on the same bytes.

    Returns the model and the parsed document (the document is what a double hashes).
    """
    document = parse_json_request(body, content_type)
    try:
        return model.model_validate_json(body), document
    except ValidationError as error:
        raise ValidationFailedError(details=details_from_pydantic(list(error.errors()))) from None


def check_test_control(request: Request, expected_key: str) -> None:
    """A `/_test/` call: the control key, and no query parameter (none of these routes declares one)."""
    check_test_control_key(request.headers.get(TEST_CONTROL_HEADER), expected_key)
    QueryParameters(request.query_params.multi_items(), ()).finish()


def check_test_control_key(presented: str | None, expected: str) -> None:
    """Allow a `/_test/` call only with the configured control key."""
    if not presented or not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        raise UnauthenticatedError


def new_service_app(title: str) -> FastAPI:
    """A double's app: no documentation routes, no slash redirection, the envelope and the middleware.

    With `redirect_slashes` off a path with a trailing slash is an unknown route, so no
    response of a double is ever a redirect (SPEC_DECISIONS 35).
    """
    app = FastAPI(title=title, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    return app


@dataclass(frozen=True)
class _Failure:
    """What a 5xx response is about: the code of its body, the exception type and the exception."""

    error_code: str
    exception_type: str
    cause: BaseException | None = None


_FAILURE = "onepledge.failure"


def failure_response(scope: Scope, error: DomainError, cause: BaseException | None = None) -> JSONResponse:
    """The envelope of an error; for a 5xx it also records what the failure is about on the request.

    Every 5xx envelope is built here, so the middleware can write the failure facts on every
    record of that response in one place (SPEC_DECISIONS 66).
    """
    request_id = scope.get(_REQUEST_ID)
    rid = request_id if isinstance(request_id, str) else str(uuid.uuid4())
    if error.http_status >= _HTTP_SERVER_ERROR:
        scope[_FAILURE] = _Failure(error.code, type(cause or error).__name__, cause)
    return error_response(error, rid)


def install_error_handlers(app: FastAPI) -> None:
    """Map every error to the contract envelope with a fixed message."""

    async def domain_error(request: Request, exc: Exception) -> Response:
        error = exc if isinstance(exc, DomainError) else DomainError()
        return failure_response(request.scope, error)

    async def validation_error(request: Request, exc: Exception) -> Response:
        errors = list(exc.errors()) if isinstance(exc, RequestValidationError) else []
        return error_response(
            ValidationFailedError(details=details_from_pydantic(errors)), request_id_of(request)
        )

    async def http_error(request: Request, exc: Exception) -> Response:
        code = getattr(exc, "status_code", _HTTP_SERVER_ERROR)
        status = code if isinstance(code, int) else _HTTP_SERVER_ERROR
        if status == _HTTP_METHOD_NOT_ALLOWED:
            # The router names the route's methods; RFC 9110 requires them in `Allow` (SPEC_DECISIONS 43.5).
            headers = getattr(exc, "headers", None) or {}
            allow = {"Allow": headers["Allow"]} if "Allow" in headers else {}
            return error_response(MethodNotAllowedError(), request_id_of(request), allow)
        if status == _HTTP_NOT_FOUND:
            return error_response(ResourceNotFoundError(), request_id_of(request))
        return failure_response(request.scope, DomainError(), exc)

    async def unexpected_error(request: Request, exc: Exception) -> Response:
        # Reached only by a defect of the middleware itself (it handles every exception the app
        # raises): the middleware has already unbound the request, so the keys are written here.
        error = DomainError()
        _log.error(
            LogEvent.UNHANDLED_EXCEPTION,
            exc_info=exc,
            error_code=error.code,
            http_status=error.http_status,
            request_id=request_id_of(request),
        )
        return error_response(error, request_id_of(request))

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


def _declared_length(scope: Scope, limit: int) -> None:
    """Refuse a malformed `Content-Length` (422) or one above the limit (413) before the body is read."""
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            parsed = parse_integer_text(value.decode("latin-1"), 0, MAX_INTEGER)
            if isinstance(parsed, Issue):
                raise ValidationFailedError(details=(ErrorDetail("Content-Length", parsed),))
            if parsed > limit:
                raise PayloadTooLargeError


@dataclass
class _Exchange:
    """What the middleware knows about the response of one request."""

    status: int = _HTTP_SERVER_ERROR
    started: bool = False


class RequestContextMiddleware:
    """Binds the request id, limits the body size, handles every failure and writes one access record.

    The request id stays bound until the failure record and the access record are written,
    and is unbound last, whatever happened (SPEC_DECISIONS 36). An exception the app raises
    is logged here, once, with the request id, and answered with the 500 envelope when no
    response has started.
    """

    def __init__(self, app: AsgiApp, max_body_bytes: int = MAX_BODY_BYTES) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Wrap one HTTP request."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request_id = _request_id_from(scope)
        scope[_REQUEST_ID] = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.monotonic()
        exchange = _Exchange()
        sending = self._with_request_id(send, request_id, exchange)
        try:
            try:
                _declared_length(scope, self._max_body_bytes)
                await self._app(scope, self._limited(receive), sending)
            except DomainError as error:
                await failure_response(scope, error)(scope, receive, sending)
            except Exception as error:  # Every failure of the app ends here (SPEC_DECISIONS 34).
                if exchange.started:
                    # Too late for the envelope: the response already went out with its own status.
                    _log.error(LogEvent.UNHANDLED_EXCEPTION, exc_info=error, http_status=exchange.status)
                else:
                    await failure_response(scope, DomainError(), error)(scope, receive, sending)
            failure = self._failure_facts(scope, exchange)
            if failure:
                self._log_failure(scope, exchange.status, failure)
            route = scope.get("route")
            _log.info(
                LogEvent.HTTP_REQUEST,
                method=scope.get("method", ""),
                route=getattr(route, "path", "unmatched"),
                http_status=exchange.status,
                duration_ms=round((time.monotonic() - started) * 1000),
                **failure,
            )
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

    @staticmethod
    def _failure_facts(scope: Scope, exchange: _Exchange) -> dict[str, str]:
        """The error code and exception type of a 5xx response, the keys every record of it carries."""
        if exchange.status < _HTTP_SERVER_ERROR:
            return {}
        failure = scope.get(_FAILURE)
        if not isinstance(failure, _Failure):
            failure = _Failure(DomainError.code, DomainError.__name__)
        return {"error_code": failure.error_code, "exception_type": failure.exception_type}

    @staticmethod
    def _log_failure(scope: Scope, status: int, facts: dict[str, str]) -> None:
        """The one failure record of a 5xx response: `error` for a 500, `warning` for any other."""
        failure = scope.get(_FAILURE)
        cause = failure.cause if isinstance(failure, _Failure) else None
        if cause is not None:
            _log.error(LogEvent.UNHANDLED_EXCEPTION, exc_info=cause, http_status=status, **facts)
        elif status == _HTTP_SERVER_ERROR:
            _log.error(LogEvent.INTERNAL_ERROR, http_status=status, **facts)
        else:
            _log.warning(LogEvent.SERVER_ERROR, http_status=status, **facts)

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
    def _with_request_id(send: Send, request_id: str, exchange: _Exchange) -> Send:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                exchange.started = True
                status = message["status"]
                exchange.status = status if isinstance(status, int) else _HTTP_SERVER_ERROR
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        return wrapped


def route_template(request: Request) -> str:
    """The route template a request matched (for example `/v1/certificates/{certificate_id}`)."""
    route = request.scope.get("route")
    return route.path if isinstance(route, APIRoute) else request.url.path


def contract_routes(app: FastAPI) -> frozenset[str]:
    """The route templates of the contract endpoints of an app (no `/healthz`, no `/_test/`)."""
    return frozenset(
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and route.path != "/healthz" and not route.path.startswith("/_test/")
    )
