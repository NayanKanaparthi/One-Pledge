"""Request ids, the access record, and the error envelope of the contract (SPEC 4, 6.2).

The access record carries the route template, never the path or the query string,
so a VIN in a URL never reaches the log.
"""

from __future__ import annotations

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

from onepledge_outside_records.domain.errors import (
    DomainError,
    MethodNotAllowedError,
    ResourceNotFoundError,
    ServiceUnavailableError,
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
    value = request.scope.get("onepledge.request_id")
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
        if isinstance(error, ServiceUnavailableError):
            _log.warning(LogEvent.DATABASE_UNAVAILABLE)
        elif error.http_status == _HTTP_SERVER_ERROR:
            # A named 500 is still one failure: one error record (SPEC_DECISIONS 34).
            _log.error(LogEvent.INTERNAL_ERROR)
        return error_response(error, request_id_of(request))

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
        return error_response(DomainError(), request_id_of(request))

    async def unexpected_error(request: Request, exc: Exception) -> Response:
        # Reached only by a defect of the middleware itself: the middleware handles every
        # exception the app raises while the request id is still bound.
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
        scope["onepledge.request_id"] = request_id
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
                await self._app(scope, receive, sending)
            except Exception as error:  # Every failure of the app ends here, logged once (SPEC_DECISIONS 34).
                _log.error(LogEvent.UNHANDLED_EXCEPTION, exc_info=error)
                if not exchange.started:
                    await error_response(DomainError(), request_id)(scope, receive, sending)
            route = scope.get("route")
            client = scope.get("onepledge.client_name")
            _log.info(
                LogEvent.HTTP_REQUEST,
                method=scope.get("method", ""),
                route=getattr(route, "path", "unmatched"),
                http_status=exchange.status,
                duration_ms=round((time.monotonic() - started) * 1000),
                client=client if isinstance(client, str) else None,
            )
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
