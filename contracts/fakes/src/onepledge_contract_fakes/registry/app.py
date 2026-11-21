"""HTTP application of the registry double (TEST DOUBLE of `contracts/registry.yaml`)."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response

from onepledge_contract_fakes.canonical_json import canonical_sha256
from onepledge_contract_fakes.clock import Clock, IdSource, RandomIds, SystemClock
from onepledge_contract_fakes.controls import SUBMISSIONS_ROUTE, ControlState, FailureWhen
from onepledge_contract_fakes.errors import ErrorDetail, InjectedFailureError, ValidationFailedError
from onepledge_contract_fakes.registry.config import RegistryConfig
from onepledge_contract_fakes.registry.control_routes import mount_test_controls
from onepledge_contract_fakes.registry.double import RegistryDouble
from onepledge_contract_fakes.registry.model import CaseStatus
from onepledge_contract_fakes.registry.wire import CasePositionRequestModel, SubmissionRequestModel
from onepledge_contract_fakes.web import (
    RequestContextMiddleware,
    install_error_handlers,
    parse_model_json,
    route_template,
)

_IDEMPOTENCY_KEY = re.compile(r"^[0-9a-f]{64}$")
_JSON = "application/json"


class _Guard:
    """Records bodies and applies injected failures before a contract call is processed."""

    def __init__(self, double: RegistryDouble, controls: ControlState | None) -> None:
        self._double = double
        self._controls = controls

    async def enter(self, request: Request) -> tuple[str, bytes]:
        """Return the authenticated member and the raw body, or raise."""
        body = await request.body()
        api_key = request.headers.get("x-api-key")
        if self._controls is not None:
            if request.method == "POST":
                self._controls.record(request.url.path, self._double.identify(api_key), body)
            status = self._controls.take(FailureWhen.BEFORE_PROCESSING, route_template(request))
            if status is not None:
                raise InjectedFailureError(status)
        return self._double.authenticate(api_key), body

    async def enter_reader(self, request: Request) -> str:
        """Like `enter` for the ledger reads, which also accept the auditor key."""
        if self._controls is not None:
            status = self._controls.take(FailureWhen.BEFORE_PROCESSING, route_template(request))
            if status is not None:
                raise InjectedFailureError(status)
        return self._double.reader_role(request.headers.get("x-api-key"))

    def after_commit(self) -> None:
        """Fail a submission after it was stored, to simulate a lost response."""
        if self._controls is not None:
            status = self._controls.take(FailureWhen.AFTER_COMMIT, SUBMISSIONS_ROUTE)
            if status is not None:
                raise InjectedFailureError(status)


def create_registry_app(
    config: RegistryConfig, clock: Clock | None = None, ids: IdSource | None = None
) -> FastAPI:
    """Build the FastAPI app around a fresh in-memory double."""
    double = RegistryDouble(config, clock or SystemClock(), ids or RandomIds())
    controls = ControlState() if config.test_controls else None
    guard = _Guard(double, controls)
    app = FastAPI(title="OnePledge registry TEST DOUBLE", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.double = double
    app.state.controls = controls
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    _mount_submissions(app, double, guard)
    _mount_reads(app, double, guard)
    if controls is not None and config.test_control_key is not None:
        mount_test_controls(app, double, controls, config.test_control_key)
    return app


def _mount_submissions(app: FastAPI, double: RegistryDouble, guard: _Guard) -> None:
    @app.post("/v1/submissions")
    async def create_submission(request: Request) -> Response:
        member_id, body = await guard.enter(request)
        key = request.headers.get("idempotency-key", "")
        if not _IDEMPOTENCY_KEY.fullmatch(key):
            raise ValidationFailedError(details=(ErrorDetail("Idempotency-Key", "PATTERN_MISMATCH"),))
        model = parse_model_json(SubmissionRequestModel, body)
        outcome = double.submit(member_id, key, model.to_submission(), canonical_sha256(json.loads(body)))
        guard.after_commit()
        headers = {"Idempotent-Replay": "true"} if outcome.replay else {}
        return Response(content=outcome.body, status_code=outcome.status, media_type=_JSON, headers=headers)

    @app.get("/v1/submissions/{submission_id}")
    async def get_submission(submission_id: str, request: Request) -> JSONResponse:
        member_id, _ = await guard.enter(request)
        return JSONResponse(double.submission(member_id, submission_id))

    @app.post("/v1/cases/{case_id}/resolution")
    async def post_case_position(case_id: str, request: Request) -> JSONResponse:
        member_id, body = await guard.enter(request)
        model = parse_model_json(CasePositionRequestModel, body)
        return JSONResponse(double.record_position(member_id, case_id, model.to_position()))

    @app.post("/v1/outbox/{message_id}/ack", status_code=204)
    async def ack_outbox_message(message_id: str, request: Request) -> Response:
        member_id, _ = await guard.enter(request)
        double.ack(member_id, message_id)
        return Response(status_code=204)


def _mount_reads(app: FastAPI, double: RegistryDouble, guard: _Guard) -> None:
    @app.get("/v1/outbox")
    async def list_outbox(request: Request, limit: Annotated[int, Query(ge=1, le=200)] = 100) -> JSONResponse:
        member_id, _ = await guard.enter(request)
        return JSONResponse(double.outbox(member_id, limit))

    @app.get("/v1/cases")
    async def list_cases(
        request: Request,
        status: CaseStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> JSONResponse:
        member_id, _ = await guard.enter(request)
        return JSONResponse(double.cases(member_id, status, limit, offset))

    @app.get("/v1/certificates/{certificate_id}")
    async def get_certificate(certificate_id: str, request: Request) -> JSONResponse:
        member_id, _ = await guard.enter(request)
        return JSONResponse(double.certificate(member_id, certificate_id))

    @app.get("/v1/ledger/events")
    async def list_ledger_events(
        request: Request,
        after_seq: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
        view: Literal["headers", "full"] = "headers",
    ) -> JSONResponse:
        reader = await guard.enter_reader(request)
        return JSONResponse(double.ledger_events(after_seq, limit, view == "full", reader))

    @app.get("/v1/checkpoints/latest")
    async def get_latest_checkpoint(request: Request) -> JSONResponse:
        await guard.enter_reader(request)
        return JSONResponse(double.latest_checkpoint())

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "registry", "test_double": True})
