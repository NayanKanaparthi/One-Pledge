"""HTTP application of the key holder double (TEST DOUBLE of `contracts/key_holder.yaml`)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from onepledge_contract_fakes.clock import Clock, SystemClock
from onepledge_contract_fakes.controls import ControlState, FailureWhen, parse_failure_spec
from onepledge_contract_fakes.errors import InjectedFailureError
from onepledge_contract_fakes.json_body import parse_json_request
from onepledge_contract_fakes.key_holder.config import KeyHolderConfig
from onepledge_contract_fakes.key_holder.double import EvaluationRequest, KeyHolderDouble
from onepledge_contract_fakes.validation import QueryParameters
from onepledge_contract_fakes.web import (
    check_test_control,
    contract_routes,
    new_service_app,
    parse_request_model,
    route_template,
)

ELEMENT_PATTERN = r"^[A-Za-z0-9+/]{43}=$"
MAX_ELEMENTS = 1000


class EvaluateRequestModel(BaseModel):
    """Wire model of `EvaluateRequest`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: uuid.UUID
    key_version: Annotated[int, Field(ge=1)] | None = None
    elements: Annotated[
        list[Annotated[str, Field(pattern=ELEMENT_PATTERN, min_length=44, max_length=44)]],
        Field(min_length=1, max_length=MAX_ELEMENTS),
    ]


def create_key_holder_app(config: KeyHolderConfig, clock: Clock | None = None) -> FastAPI:
    """Build the FastAPI app around a fresh in-memory double."""
    double = KeyHolderDouble(config, clock or SystemClock())
    controls = ControlState() if config.test_controls else None
    app = new_service_app("OnePledge key holder TEST DOUBLE")
    app.state.double = double
    app.state.controls = controls

    @app.post("/v1/evaluate")
    async def evaluate(request: Request) -> JSONResponse:
        body = await request.body()
        api_key = request.headers.get("x-api-key")
        if controls is not None:
            controls.record(request.url.path, double.identify(api_key), body)
            status = controls.take(FailureWhen.BEFORE_PROCESSING, route_template(request))
            if status is not None:
                raise InjectedFailureError(status)
        member_id = double.authenticate(api_key)
        QueryParameters(request.query_params.multi_items(), ()).finish()
        model, _ = parse_request_model(EvaluateRequestModel, body, request.headers.get("content-type"))
        command = EvaluationRequest(str(model.request_id), model.key_version, tuple(model.elements))
        result = double.evaluate(member_id, command)
        return JSONResponse(
            {
                "request_id": result.request_id,
                "holder_id": result.holder_id,
                "key_version": result.key_version,
                "elements": list(result.elements),
            }
        )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        # Probes add cache busters: health ignores its query (SPEC_DECISIONS 41).
        return JSONResponse(
            {
                "status": "ok",
                "service": "key_holder",
                "holder_id": double.holder_id,
                "active_key_version": double.active_version,
                "test_double": True,
            }
        )

    if controls is not None and config.test_control_key is not None:
        _mount_controls(app, double, controls, config.test_control_key)
    return app


def _mount_controls(app: FastAPI, double: KeyHolderDouble, controls: ControlState, key: str) -> None:
    @app.get("/_test/stats")
    async def stats(request: Request) -> JSONResponse:
        check_test_control(request, key)
        return JSONResponse(double.stats())

    @app.get("/_test/received")
    async def received(request: Request) -> JSONResponse:
        check_test_control(request, key)
        items = [{"path": r.path, "member_id": r.member_id, "body": r.body} for r in controls.received()]
        return JSONResponse({"requests": items})

    @app.post("/_test/fail-next")
    async def fail_next(request: Request) -> JSONResponse:
        check_test_control(request, key)
        document = parse_json_request(await request.body(), request.headers.get("content-type"))
        spec = parse_failure_spec(document, contract_routes(app), allow_after_commit=False)
        controls.arm(spec)
        return JSONResponse(
            {"armed": spec.count, "status": spec.status, "when": spec.when.value, "path": spec.path}
        )
