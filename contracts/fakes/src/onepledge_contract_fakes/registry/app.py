"""HTTP application of the registry double (TEST DOUBLE of `contracts/registry.yaml`)."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from onepledge_contract_fakes.canonical_json import canonical_sha256
from onepledge_contract_fakes.clock import Clock, IdSource, RandomIds, SystemClock
from onepledge_contract_fakes.controls import SUBMISSIONS_ROUTE, ControlState, FailureWhen
from onepledge_contract_fakes.errors import ErrorDetail, InjectedFailureError, ValidationFailedError
from onepledge_contract_fakes.registry.config import RegistryConfig
from onepledge_contract_fakes.registry.control_routes import mount_test_controls
from onepledge_contract_fakes.registry.double import RegistryDouble
from onepledge_contract_fakes.registry.inspection import MAX_SEQ
from onepledge_contract_fakes.registry.model import CaseStatus
from onepledge_contract_fakes.registry.wire import CasePositionRequestModel, SubmissionRequestModel
from onepledge_contract_fakes.validation import MAX_OFFSET, Issue, QueryParameters
from onepledge_contract_fakes.web import (
    RequestContextMiddleware,
    install_error_handlers,
    parse_model_json,
    route_template,
)

_IDEMPOTENCY_KEY = re.compile(r"^[0-9a-f]{64}$")
_JSON = "application/json"


@dataclass(frozen=True)
class _Entry:
    """An admitted contract call: the caller, the raw body and the checked query string."""

    caller: str
    body: bytes
    query: QueryParameters


class _Guard:
    """Records bodies, applies injected failures and checks the query string of a contract call."""

    def __init__(self, double: RegistryDouble, controls: ControlState | None) -> None:
        self._double = double
        self._controls = controls

    def _inject(self, request: Request) -> None:
        if self._controls is not None:
            status = self._controls.take(FailureWhen.BEFORE_PROCESSING, route_template(request))
            if status is not None:
                raise InjectedFailureError(status)

    @staticmethod
    def _query(request: Request, declared: Collection[str]) -> QueryParameters:
        query = QueryParameters(request.query_params.multi_items(), declared)
        if not declared:
            query.finish()
        return query

    async def enter(self, request: Request, declared: Collection[str] = ()) -> _Entry:
        """Authenticate a member; a route without query parameters refuses any."""
        body = await request.body()
        api_key = request.headers.get("x-api-key")
        if self._controls is not None and request.method == "POST":
            self._controls.record(request.url.path, self._double.identify(api_key), body)
        self._inject(request)
        member_id = self._double.authenticate(api_key)
        return _Entry(member_id, body, self._query(request, declared))

    async def enter_reader(self, request: Request, declared: Collection[str] = ()) -> _Entry:
        """Like `enter` for the ledger reads, which also accept the auditor key."""
        self._inject(request)
        reader = self._double.reader_role(request.headers.get("x-api-key"))
        return _Entry(reader, b"", self._query(request, declared))

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
        entry = await guard.enter(request)
        key = request.headers.get("idempotency-key", "")
        if not _IDEMPOTENCY_KEY.fullmatch(key):
            raise ValidationFailedError(details=(ErrorDetail("Idempotency-Key", Issue.PATTERN_MISMATCH),))
        model = parse_model_json(SubmissionRequestModel, entry.body)
        # The strict model refuses every float, so the body hashed here is canonical JSON input.
        body_hash = canonical_sha256(json.loads(entry.body))
        outcome = double.submit(entry.caller, key, model.to_submission(), body_hash)
        guard.after_commit()
        headers = {"Idempotent-Replay": "true"} if outcome.replay else {}
        return Response(content=outcome.body, status_code=outcome.status, media_type=_JSON, headers=headers)

    @app.get("/v1/submissions/{submission_id}")
    async def get_submission(submission_id: str, request: Request) -> JSONResponse:
        entry = await guard.enter(request)
        return JSONResponse(double.submission(entry.caller, submission_id))

    @app.post("/v1/cases/{case_id}/resolution")
    async def post_case_position(case_id: str, request: Request) -> JSONResponse:
        entry = await guard.enter(request)
        model = parse_model_json(CasePositionRequestModel, entry.body)
        return JSONResponse(double.record_position(entry.caller, case_id, model.to_position()))

    @app.post("/v1/outbox/{message_id}/ack", status_code=204)
    async def ack_outbox_message(message_id: str, request: Request) -> Response:
        entry = await guard.enter(request)
        double.ack(entry.caller, message_id)
        return Response(status_code=204)


def _mount_reads(app: FastAPI, double: RegistryDouble, guard: _Guard) -> None:
    @app.get("/v1/outbox")
    async def list_outbox(request: Request) -> JSONResponse:
        entry = await guard.enter(request, ("limit",))
        limit = entry.query.integer("limit", 100, 1, 200)
        entry.query.finish()
        return JSONResponse(double.outbox(entry.caller, limit))

    @app.get("/v1/cases")
    async def list_cases(request: Request) -> JSONResponse:
        entry = await guard.enter(request, ("status", "limit", "offset"))
        status = entry.query.choice("status", [s.value for s in CaseStatus])
        limit = entry.query.integer("limit", 50, 1, 200)
        offset = entry.query.integer("offset", 0, 0, MAX_OFFSET)
        entry.query.finish()
        case_status = CaseStatus(status) if status is not None else None
        return JSONResponse(double.cases(entry.caller, case_status, limit, offset))

    @app.get("/v1/certificates/{certificate_id}")
    async def get_certificate(certificate_id: str, request: Request) -> JSONResponse:
        entry = await guard.enter(request)
        return JSONResponse(double.certificate(entry.caller, certificate_id))

    @app.get("/v1/ledger/events")
    async def list_ledger_events(request: Request) -> JSONResponse:
        entry = await guard.enter_reader(request, ("after_seq", "limit", "view"))
        after_seq = entry.query.integer("after_seq", 0, 0, MAX_SEQ)
        limit = entry.query.integer("limit", 500, 1, 1000)
        view = entry.query.choice("view", ("headers", "full")) or "headers"
        entry.query.finish()
        return JSONResponse(double.ledger_events(after_seq, limit, view == "full", entry.caller))

    @app.get("/v1/checkpoints/latest")
    async def get_latest_checkpoint(request: Request) -> JSONResponse:
        await guard.enter_reader(request)
        return JSONResponse(double.latest_checkpoint())

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        QueryParameters(request.query_params.multi_items(), ()).finish()
        return JSONResponse({"status": "ok", "service": "registry", "test_double": True})
