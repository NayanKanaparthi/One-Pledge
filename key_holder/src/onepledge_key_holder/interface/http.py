"""The FastAPI application of the key holder (`contracts/key_holder.yaml`).

`POST /v1/evaluate` and `GET /healthz`. Every body is a schema of the contract: the
`EvaluateResponse`, the `Health` schema without `test_double` (SPEC_DECISIONS 11), or the
`ErrorResponse` envelope, also for an unknown route (404) and a wrong method (405). The app
never redirects (a trailing slash is not rewritten) and publishes no generated
documentation routes.

`evaluate` checks in the layer 1 double's order: the API key, the body size, the
Content-Type, the JSON, unknown fields, then the fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from structlog.typing import FilteringBoundLogger

from onepledge_key_holder.application.authenticate_member import AuthenticateMember
from onepledge_key_holder.application.evaluate_blinded import EvaluateBlinded
from onepledge_key_holder.application.report_health import ReportHealth
from onepledge_key_holder.domain.errors import KeyHolderError
from onepledge_key_holder.interface.plumbing import (
    HTTP_METHOD_NOT_ALLOWED,
    HTTP_NOT_FOUND,
    HTTP_SERVER_ERROR,
    RequestContextMiddleware,
    api_error,
    envelope,
    read_limited_body,
    request_id_of,
    single_header,
)
from onepledge_key_holder.interface.wire import ApiError, parse_evaluate_request, parse_json_object

SERVICE = "key_holder"
EVALUATE_ROUTE = "/v1/evaluate"
# Routing errors are handled by status code, so the envelope is used for every one of them.
_HTTP_ERROR_STATUSES = tuple(
    status.value for status in HTTPStatus if status >= HTTPStatus.BAD_REQUEST and status != HTTP_SERVER_ERROR
)


@dataclass(frozen=True)
class UseCases:
    """The three use cases the routes call."""

    report_health: ReportHealth
    authenticate: AuthenticateMember
    evaluate: EvaluateBlinded


def _install_error_handlers(app: FastAPI) -> None:
    async def business_error(request: Request, exc: Exception) -> Response:
        error = (
            api_error(exc)
            if isinstance(exc, KeyHolderError)
            else ApiError(HTTP_SERVER_ERROR, "INTERNAL_ERROR")
        )
        return envelope(error, request_id_of(request.scope))

    async def wire_error(request: Request, exc: Exception) -> Response:
        error = exc if isinstance(exc, ApiError) else ApiError(HTTP_SERVER_ERROR, "INTERNAL_ERROR")
        return envelope(error, request_id_of(request.scope))

    async def http_error(request: Request, exc: Exception) -> Response:
        status = getattr(exc, "status_code", HTTP_SERVER_ERROR)
        request_id = request_id_of(request.scope)
        if status == HTTP_METHOD_NOT_ALLOWED:
            headers = getattr(exc, "headers", None) or {}
            allow = {"Allow": headers["Allow"]} if "Allow" in headers else {}
            return envelope(ApiError(HTTP_METHOD_NOT_ALLOWED, "METHOD_NOT_ALLOWED"), request_id, allow)
        if status == HTTP_NOT_FOUND:
            return envelope(ApiError(HTTP_NOT_FOUND, "NOT_FOUND"), request_id)
        return envelope(ApiError(HTTP_SERVER_ERROR, "INTERNAL_ERROR"), request_id)

    app.add_exception_handler(KeyHolderError, business_error)
    app.add_exception_handler(ApiError, wire_error)
    for status in _HTTP_ERROR_STATUSES:
        app.add_exception_handler(status, http_error)


def create_app(use_cases: UseCases, log: FilteringBoundLogger) -> FastAPI:
    """The key holder app around its use cases."""
    app = FastAPI(
        title="OnePledge key holder",
        redirect_slashes=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    _install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware, log=log)

    @app.post(EVALUATE_ROUTE)
    async def evaluate(request: Request) -> JSONResponse:
        api_key = single_header(request, "X-API-Key")
        member_id = await run_in_threadpool(use_cases.authenticate.run, api_key)
        body = await read_limited_body(request)
        document = parse_json_object(body, single_header(request, "Content-Type"))
        command = parse_evaluate_request(document, has_query=len(request.query_params) > 0)
        result = await run_in_threadpool(use_cases.evaluate.run, member_id, command)
        return JSONResponse(
            {
                "request_id": result.request_id,
                "holder_id": result.holder_id.value,
                "key_version": result.key_version.number,
                "elements": list(result.elements),
            }
        )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        report = use_cases.report_health.run()
        return JSONResponse(
            {
                "status": "ok",
                "service": SERVICE,
                "holder_id": report.holder_id.value,
                "active_key_version": report.active_key_version.number,
            }
        )

    return app
