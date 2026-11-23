"""Request ids, the access record, and the error envelope of the contract (SPEC 4, 6.2).

The access record carries the route template, never the path or the query string,
so a VIN in a URL never reaches the log.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.requests import ClientDisconnect

from onepledge_outside_records.domain.errors import (
    DomainError,
    ErrorDetail,
    MethodNotAllowedError,
    ResourceNotFoundError,
    ServiceUnavailableError,
    UnauthenticatedError,
    ValidationFailedError,
)
from onepledge_outside_records.infrastructure.logging_setup import LogEvent, get_logger

REQUEST_ID_HEADER = "X-Request-ID"
_HTTP_NOT_FOUND, _HTTP_METHOD_NOT_ALLOWED, _HTTP_SERVER_ERROR = 404, 405, 500
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

_log = get_logger(__name__)


def request_id_of(request: Request) -> str:
    """The request id bound by the middleware."""
    value = request.scope.get(_REQUEST_ID)
    return value if isinstance(value, str) else str(uuid.uuid4())


def error_response(
    error: DomainError, request_id: str, extra_headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """The `ErrorResponse` envelope with the fixed message of the code."""
    body: dict[str, Any] = {"code": error.code, "message": error.message, "request_id": request_id}
    if error.details:
        body["details"] = [{"field": d.field, "issue": d.issue} for d in error.details]
    headers = {REQUEST_ID_HEADER: request_id, **(extra_headers or {})}
    return JSONResponse(status_code=error.http_status, content={"error": body}, headers=headers)


@dataclass(frozen=True)
class _Failure:
    """What a 5xx response is about: its event, the code of its body, the exception type and the exception."""

    event: LogEvent
    error_code: str
    exception_type: str
    cause: BaseException | None = None


_FAILURE = "onepledge.failure"
_REQUEST_ID = "onepledge.request_id"


def failure_response(scope: Scope, error: DomainError, cause: BaseException | None = None) -> JSONResponse:
    """The envelope of an error; for a 5xx it also records what the failure is about on the request.

    Every 5xx envelope is built here, so the middleware writes the failure facts on every
    record of that response in one place (SPEC_DECISIONS 66).
    """
    if error.http_status >= _HTTP_SERVER_ERROR:
        if cause is not None:
            event = LogEvent.UNHANDLED_EXCEPTION
        elif isinstance(error, ServiceUnavailableError):
            event = LogEvent.DATABASE_UNAVAILABLE
        else:
            event = (
                LogEvent.INTERNAL_ERROR if error.http_status == _HTTP_SERVER_ERROR else LogEvent.SERVER_ERROR
            )
        scope[_FAILURE] = _Failure(event, error.code, type(cause or error).__name__, cause)
    request_id = scope.get(_REQUEST_ID)
    return error_response(error, request_id if isinstance(request_id, str) else str(uuid.uuid4()))


def failure_facts(scope: Scope, status: int) -> dict[str, str]:
    """The error code and exception type every record of a 5xx response carries; empty below 500."""
    if status < _HTTP_SERVER_ERROR:
        return {}
    failure = scope.get(_FAILURE)
    if not isinstance(failure, _Failure):
        return {"error_code": DomainError.code, "exception_type": DomainError.__name__}
    return {"error_code": failure.error_code, "exception_type": failure.exception_type}


def log_failure(scope: Scope, status: int) -> None:
    """The one failure record of a 5xx response: `error` for a 500, `warning` for any other status."""
    facts = failure_facts(scope, status)
    failure = scope.get(_FAILURE)
    event = failure.event if isinstance(failure, _Failure) else LogEvent.INTERNAL_ERROR
    cause = failure.cause if isinstance(failure, _Failure) else None
    if cause is not None:
        _log.error(event, exc_info=cause, http_status=status, **facts)
    elif status == _HTTP_SERVER_ERROR:
        _log.error(event, http_status=status, **facts)
    else:
        _log.warning(event, http_status=status, **facts)


def new_service_app(title: str) -> FastAPI:
    """The app: no documentation routes, no slash redirection, the envelope and the middleware.

    With `redirect_slashes` off a path with a trailing slash is an unknown route, so no
    response is ever a redirect (SPEC_DECISIONS 35).
    """
    app = FastAPI(title=title, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    install_error_handlers(app)
    app.add_middleware(AccessLogMiddleware)
    return app


def install_error_handlers(app: FastAPI) -> None:
    """Every error becomes the envelope; nothing from a library or a request reaches the body."""

    async def domain_error(request: Request, exc: Exception) -> Response:
        error = exc if isinstance(exc, DomainError) else DomainError()
        return failure_response(request.scope, error)

    async def validation_error(request: Request, exc: Exception) -> Response:
        return error_response(ValidationFailedError(), request_id_of(request))

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


# SPEC_DECISIONS 112.8: a repeated credential is 401; any of these repeated is 422 DUPLICATE.
AUTHENTICATION_HEADERS = ("Authorization", "X-API-Key", "X-Test-Control-Key")
SINGLE_HEADERS = ("Idempotency-Key", "Content-Type", "Content-Length")


def header(request: Request, name: str) -> str | None:
    """The one value of a header; a repeated credential is UNAUTHENTICATED, any other DUPLICATE."""
    values = request.headers.getlist(name)
    if len(values) > 1:
        if name in AUTHENTICATION_HEADERS:
            raise UnauthenticatedError
        raise ValidationFailedError((ErrorDetail(name, "DUPLICATE"),))
    return values[0] if values else None


def refuse_repeated_headers(request: Request) -> None:
    """After authentication: a repeated credential is 401 and any other repeated header 422."""
    if any(len(request.headers.getlist(name)) > 1 for name in AUTHENTICATION_HEADERS):
        raise UnauthenticatedError
    repeated = [
        ErrorDetail(name, "DUPLICATE") for name in SINGLE_HEADERS if len(request.headers.getlist(name)) > 1
    ]
    if repeated:
        raise ValidationFailedError(tuple(repeated))


_CANONICAL_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _request_id_from(scope: Scope) -> str:
    """The caller's X-Request-ID when it is a canonical UUID, otherwise a new one (SPEC_DECISIONS 75)."""
    for name, value in scope.get("headers", []):
        if name.lower() == b"x-request-id":
            text = bytes(value).decode("latin-1")
            if _CANONICAL_UUID.fullmatch(text):
                return text
            break
    return str(uuid.uuid4())


@dataclass
class _Exchange:
    """What the middleware knows about the response of one request."""

    status: int = _HTTP_SERVER_ERROR
    started: bool = False


class AccessLogMiddleware:
    """Binds the request id, echoes it, handles every failure and writes one access record.

    The request id stays bound until the failure record and the access record are written
    and is unbound last, whatever happened (SPEC_DECISIONS 36). An exception the app raises
    is logged here, once, with the request id, and answered with the 500 envelope when no
    response has started.
    """

    def __init__(self, app: AsgiApp) -> None:
        self._app = app

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

        async def sending(message: Message) -> None:
            if message["type"] == "http.response.start":
                exchange.started = True
                status = message["status"]
                exchange.status = status if isinstance(status, int) else _HTTP_SERVER_ERROR
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            try:
                if sum(1 for key, _ in scope.get("headers", []) if bytes(key).lower() == b"x-request-id") > 1:
                    # A repeated request id is refused before routing (SPEC_DECISIONS 112.8).
                    raise ValidationFailedError((ErrorDetail("X-Request-ID", "DUPLICATE"),))
                await self._app(scope, receive, sending)
            except DomainError as error:
                await failure_response(scope, error)(scope, receive, sending)
            except ClientDisconnect:
                # The client hung up before the request was complete: not a server failure, so
                # one info record and no response, failure record or access record (decision 75).
                _log.info(LogEvent.CLIENT_DISCONNECTED, method=scope.get("method", ""))
                return
            except Exception as error:  # Every failure of the app ends here (SPEC_DECISIONS 34).
                if exchange.started:
                    # Too late for the envelope: the response already went out with its own status.
                    _log.error(LogEvent.UNHANDLED_EXCEPTION, exc_info=error, http_status=exchange.status)
                else:
                    await failure_response(scope, DomainError(), error)(scope, receive, sending)
            if exchange.status >= _HTTP_SERVER_ERROR:
                log_failure(scope, exchange.status)
            route = scope.get("route")
            client = scope.get("onepledge.client_name")
            _log.info(
                LogEvent.HTTP_REQUEST,
                method=scope.get("method", ""),
                route=getattr(route, "path", "unmatched"),
                http_status=exchange.status,
                duration_ms=round((time.monotonic() - started) * 1000),
                client=client if isinstance(client, str) else None,
                **failure_facts(scope, exchange.status),
            )
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
