"""The request middleware and the assembly of a double's app (TEST DOUBLES).

The middleware binds the request id, limits the body, answers every failure the app raises
and writes the failure record and the one access record of each request.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import structlog
from fastapi import FastAPI
from starlette.requests import ClientDisconnect

from onepledge_contract_fakes.errors import (
    DomainError,
    ErrorDetail,
    PayloadTooLargeError,
    ValidationFailedError,
)
from onepledge_contract_fakes.logging_setup import LogEvent, get_logger
from onepledge_contract_fakes.validation import MAX_INTEGER, Issue, is_canonical_uuid, parse_integer_text
from onepledge_contract_fakes.web.envelope import Failure, failure_response, install_error_handlers
from onepledge_contract_fakes.web.scope import (
    FAILURE_KEY,
    HTTP_SERVER_ERROR,
    MAX_BODY_BYTES,
    REQUEST_ID_KEY,
    AsgiApp,
    Message,
    Receive,
    Scope,
    Send,
)

_log = get_logger(__name__)


def new_service_app(title: str) -> FastAPI:
    """A double's app: no documentation routes, no slash redirection, the envelope and the middleware.

    With `redirect_slashes` off a path with a trailing slash is an unknown route, so no
    response of a double is ever a redirect (SPEC_DECISIONS 35).
    """
    app = FastAPI(title=title, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    return app


def _count(scope: Scope, name: bytes) -> int:
    return sum(1 for key, _ in scope.get("headers", []) if bytes(key).lower() == name)


def _request_id_from(scope: Scope) -> str:
    """The caller's X-Request-ID when it is a canonical UUID, otherwise a new one (SPEC_DECISIONS 75)."""
    for name, value in scope.get("headers", []):
        if name.lower() == b"x-request-id":
            text = bytes(value).decode("latin-1")
            if is_canonical_uuid(text):
                return text
            break
    return str(uuid.uuid4())


def _declared_length_problem(scope: Scope, limit: int) -> DomainError | None:
    """A repeated or malformed `Content-Length` (422) or one over the limit (413), raised on reading."""
    if _count(scope, b"content-length") > 1:
        return ValidationFailedError(details=(ErrorDetail("Content-Length", Issue.DUPLICATE),))
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            parsed = parse_integer_text(value.decode("latin-1"), 0, MAX_INTEGER)
            if isinstance(parsed, Issue):
                return ValidationFailedError(details=(ErrorDetail("Content-Length", parsed),))
            if parsed > limit:
                return PayloadTooLargeError()
    return None


@dataclass
class _Exchange:
    """What the middleware knows about the response of one request."""

    status: int = HTTP_SERVER_ERROR
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
        scope[REQUEST_ID_KEY] = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.monotonic()
        exchange = _Exchange()
        sending = self._with_request_id(send, request_id, exchange)
        try:
            try:
                if _count(scope, b"x-request-id") > 1:
                    # A repeated request id is refused before routing (SPEC_DECISIONS 112.8).
                    raise ValidationFailedError(details=(ErrorDetail("X-Request-ID", Issue.DUPLICATE),))
                problem = _declared_length_problem(scope, self._max_body_bytes)
                await self._app(scope, self._limited(receive, problem), sending)
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
        if exchange.status < HTTP_SERVER_ERROR:
            return {}
        failure = scope.get(FAILURE_KEY)
        if not isinstance(failure, Failure):
            failure = Failure(DomainError.code, DomainError.__name__)
        return {"error_code": failure.error_code, "exception_type": failure.exception_type}

    @staticmethod
    def _log_failure(scope: Scope, status: int, facts: dict[str, str]) -> None:
        """The one failure record of a 5xx response: `error` for a 500, `warning` for any other."""
        failure = scope.get(FAILURE_KEY)
        cause = failure.cause if isinstance(failure, Failure) else None
        if cause is not None:
            _log.error(LogEvent.UNHANDLED_EXCEPTION, exc_info=cause, http_status=status, **facts)
        elif status == HTTP_SERVER_ERROR:
            _log.error(LogEvent.INTERNAL_ERROR, http_status=status, **facts)
        else:
            _log.warning(LogEvent.SERVER_ERROR, http_status=status, **facts)

    def _limited(self, receive: Receive, problem: DomainError | None) -> Receive:
        """The body stream, refused at its first read when the declared length is bad or too large.

        The refusal waits for the read so that a route authenticates first (SPEC_DECISIONS 66).
        """
        received = 0
        limit = self._max_body_bytes

        async def wrapped() -> Message:
            nonlocal received
            if problem is not None:
                raise problem
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
                exchange.status = status if isinstance(status, int) else HTTP_SERVER_ERROR
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        return wrapped
