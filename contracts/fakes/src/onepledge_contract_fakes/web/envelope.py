"""The contract error envelope and the app's error handlers (TEST DOUBLES).

Every envelope is built here; a 5xx envelope also leaves its facts on the request scope, so
the middleware writes them on every record of that response (SPEC_DECISIONS 66).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from onepledge_contract_fakes.errors import (
    DomainError,
    MethodNotAllowedError,
    ResourceNotFoundError,
    ValidationFailedError,
)
from onepledge_contract_fakes.logging_setup import LogEvent, get_logger
from onepledge_contract_fakes.web.requests import details_from_pydantic
from onepledge_contract_fakes.web.scope import (
    FAILURE_KEY,
    HTTP_METHOD_NOT_ALLOWED,
    HTTP_NOT_FOUND,
    HTTP_SERVER_ERROR,
    REQUEST_ID_HEADER,
    REQUEST_ID_KEY,
    Scope,
    request_id_of,
)

# Routing and framework errors (404, 405, 413 and the like) are handled by status code, so the
# envelope is used without importing the web framework's internals. 500 stays with the
# catch-all handler, which logs the exception.
_HTTP_ERROR_STATUSES = tuple(
    status.value for status in HTTPStatus if status >= HTTPStatus.BAD_REQUEST and status != HTTP_SERVER_ERROR
)

_log = get_logger(__name__)


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


@dataclass(frozen=True)
class Failure:
    """What a 5xx response is about: the code of its body, the exception type and the exception."""

    error_code: str
    exception_type: str
    cause: BaseException | None = None


def failure_response(scope: Scope, error: DomainError, cause: BaseException | None = None) -> JSONResponse:
    """The envelope of an error; for a 5xx it also records what the failure is about on the request.

    Every 5xx envelope is built here, so the middleware can write the failure facts on every
    record of that response in one place (SPEC_DECISIONS 66).
    """
    request_id = scope.get(REQUEST_ID_KEY)
    rid = request_id if isinstance(request_id, str) else str(uuid.uuid4())
    if error.http_status >= HTTP_SERVER_ERROR:
        scope[FAILURE_KEY] = Failure(error.code, type(cause or error).__name__, cause)
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
        code = getattr(exc, "status_code", HTTP_SERVER_ERROR)
        status = code if isinstance(code, int) else HTTP_SERVER_ERROR
        if status == HTTP_METHOD_NOT_ALLOWED:
            # The router names the route's methods; RFC 9110 requires them in `Allow` (SPEC_DECISIONS 43.5).
            headers = getattr(exc, "headers", None) or {}
            allow = {"Allow": headers["Allow"]} if "Allow" in headers else {}
            return error_response(MethodNotAllowedError(), request_id_of(request), allow)
        if status == HTTP_NOT_FOUND:
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
