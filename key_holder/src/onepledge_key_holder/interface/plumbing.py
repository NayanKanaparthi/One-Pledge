"""HTTP plumbing of the key holder: the request id, the error envelope, the access record.

`RequestContextMiddleware` is plain ASGI. It gives every request an id (the caller's
`X-Request-ID` when it is a canonical UUID, otherwise a new one), puts that id on every
response, answers any exception the app raises with the 500 envelope, and writes one access
record per request holding the route template, never the path or the query (decision 6).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from structlog.typing import FilteringBoundLogger

from onepledge_key_holder.domain.errors import (
    InvalidElementError,
    KeyHolderError,
    KeyVersionUnavailableError,
    RateLimitedError,
    ServiceUnavailableError,
    UnauthenticatedError,
)
from onepledge_key_holder.infrastructure.logging_setup import HTTP_REQUEST, UNHANDLED_EXCEPTION
from onepledge_key_holder.interface.wire import (
    MAX_BODY_BYTES,
    MESSAGES,
    ApiError,
    Detail,
    is_canonical_uuid,
    validation_error,
)

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_KEY = "onepledge.request_id"
HTTP_NOT_FOUND = 404
HTTP_METHOD_NOT_ALLOWED = 405
HTTP_SERVER_ERROR = 500
# A repeated credential is 401; any other repeated header is 422 DUPLICATE (the double's rule).
_CREDENTIAL_HEADERS = frozenset({"x-api-key", "authorization"})

type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def request_id_of(scope: Mapping[str, Any]) -> str:
    """The request id the middleware bound."""
    value = scope.get(REQUEST_ID_KEY)
    return value if isinstance(value, str) else str(uuid.uuid4())


def envelope(error: ApiError, request_id: str, headers: Mapping[str, str] | None = None) -> JSONResponse:
    """`ErrorResponse`: `code`, the fixed `message`, `request_id`, and `details` when there are any."""
    body: dict[str, object] = {"code": error.code, "message": MESSAGES[error.code], "request_id": request_id}
    if error.details:
        body["details"] = [{"field": detail.field, "issue": detail.issue} for detail in error.details]
    out = dict(headers or {})
    if error.retry_after_seconds is not None:
        out["Retry-After"] = str(error.retry_after_seconds)
    return JSONResponse(status_code=error.status, content={"error": body}, headers=out)


def api_error(error: KeyHolderError) -> ApiError:
    """The contract status and code of a business error."""
    if isinstance(error, UnauthenticatedError):
        return ApiError(401, "UNAUTHENTICATED")
    if isinstance(error, InvalidElementError):
        details = [Detail(f"elements[{index}]", "NOT_A_VALID_POINT") for index in error.indexes]
        return ApiError(422, "INVALID_ELEMENT", details)
    if isinstance(error, KeyVersionUnavailableError):
        return ApiError(409, "KEY_VERSION_UNAVAILABLE")
    if isinstance(error, RateLimitedError):
        return ApiError(429, "RATE_LIMITED", retry_after_seconds=error.retry_after_seconds)
    if isinstance(error, ServiceUnavailableError):
        return ApiError(503, "SERVICE_UNAVAILABLE")
    return ApiError(HTTP_SERVER_ERROR, "INTERNAL_ERROR")


def single_header(request: Request, name: str) -> str | None:
    """The one value of a header. A repeated credential is 401, any other repeated header 422."""
    values = request.headers.getlist(name)
    if len(values) > 1:
        if name.lower() in _CREDENTIAL_HEADERS:
            raise UnauthenticatedError
        raise validation_error(Detail(name, "DUPLICATE"))
    return values[0] if values else None


async def read_limited_body(request: Request) -> bytes:
    """The whole body, or 413 PAYLOAD_TOO_LARGE once it declares or carries more than the limit."""
    declared = single_header(request, "Content-Length")
    if declared is not None and declared.isascii() and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE")
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_BODY_BYTES:
            raise ApiError(413, "PAYLOAD_TOO_LARGE")
        chunks.append(chunk)
    return b"".join(chunks)


def _header_values(scope: Scope, name: bytes) -> list[str]:
    return [bytes(value).decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == name]


class RequestContextMiddleware:
    """Binds the request id, answers every unhandled failure, and writes one access record."""

    def __init__(self, app: AsgiApp, log: FilteringBoundLogger) -> None:
        self._app = app
        self._log = log

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Wrap one HTTP request."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        presented = _header_values(scope, b"x-request-id")
        request_id = presented[0] if presented and is_canonical_uuid(presented[0]) else str(uuid.uuid4())
        scope[REQUEST_ID_KEY] = request_id
        started = time.monotonic()
        status: list[int] = []

        async def sending(message: Message) -> None:
            if message["type"] == "http.response.start":
                status.append(int(message["status"]))
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            if len(presented) > 1:
                refusal = validation_error(Detail(REQUEST_ID_HEADER, "DUPLICATE"))
                await envelope(refusal, request_id)(scope, receive, sending)
            else:
                await self._app(scope, receive, sending)
        except Exception as error:  # Every failure of the app ends here, as one 500 envelope.
            self._log.error(UNHANDLED_EXCEPTION, exception_type=type(error).__name__, request_id=request_id)
            if not status:
                failure = ApiError(HTTP_SERVER_ERROR, "INTERNAL_ERROR")
                await envelope(failure, request_id)(scope, receive, sending)
        route = scope.get("route")
        self._log.info(
            HTTP_REQUEST,
            method=scope.get("method", ""),
            route=getattr(route, "path", "unmatched"),
            http_status=status[0] if status else HTTP_SERVER_ERROR,
            duration_ms=round((time.monotonic() - started) * 1000),
            request_id=request_id,
        )
