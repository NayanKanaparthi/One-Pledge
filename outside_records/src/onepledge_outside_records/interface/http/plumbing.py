"""Request ids, the access record, and the error envelope of the contract (SPEC 4, 6.2).

The access record carries the route template, never the path or the query string,
so a VIN in a URL never reaches the log.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
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
    int(status) for status in HTTPStatus if status >= HTTPStatus.BAD_REQUEST and status != _HTTP_SERVER_ERROR
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


def error_response(error: DomainError, request_id: str) -> JSONResponse:
    """The `ErrorResponse` envelope with the fixed message of the code."""
    body: dict[str, Any] = {"code": error.code, "message": error.message, "request_id": request_id}
    if error.details:
        body["details"] = [{"field": d.field, "issue": d.issue} for d in error.details]
    return JSONResponse(
        status_code=error.http_status, content={"error": body}, headers={REQUEST_ID_HEADER: request_id}
    )


def install_error_handlers(app: FastAPI) -> None:
    """Every error becomes the envelope; nothing from a library or a request reaches the body."""

    async def domain_error(request: Request, exc: Exception) -> Response:
        error = exc if isinstance(exc, DomainError) else DomainError()
        if isinstance(error, ServiceUnavailableError):
            _log.warning(LogEvent.DATABASE_UNAVAILABLE)
        return error_response(error, request_id_of(request))

    async def validation_error(request: Request, exc: Exception) -> Response:
        return error_response(ValidationFailedError(), request_id_of(request))

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


class AccessLogMiddleware:
    """Binds the request id, echoes it, and writes one access record per request."""

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
        status = {"value": _HTTP_SERVER_ERROR}

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["value"] = int(message["status"])
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self._app(scope, receive, wrapped)
        finally:
            route = scope.get("route")
            client = scope.get("onepledge.client_name")
            _log.info(
                LogEvent.HTTP_REQUEST,
                method=scope.get("method", ""),
                route=getattr(route, "path", "unmatched"),
                http_status=status["value"],
                duration_ms=int((time.monotonic() - started) * 1000),
                client=client if isinstance(client, str) else None,
            )
            structlog.contextvars.unbind_contextvars("request_id")
