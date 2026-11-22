"""The `/_test/` endpoints of the registry test double (TEST CONTROLS, SPEC 7.3, not in any contract)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from onepledge_contract_fakes.controls import ControlState, parse_failure_spec
from onepledge_contract_fakes.json_body import parse_json_request
from onepledge_contract_fakes.registry.double import RegistryDouble
from onepledge_contract_fakes.registry.inspection import parse_tamper_request
from onepledge_contract_fakes.web import (
    check_test_control,
    contract_routes,
)


def mount_test_controls(app: FastAPI, double: RegistryDouble, controls: ControlState, key: str) -> None:
    """Add the test control routes, each guarded by the test control key."""

    def guard(request: Request) -> None:
        check_test_control(request, key)

    @app.get("/_test/stats")
    async def stats(request: Request) -> JSONResponse:
        guard(request)
        return JSONResponse(double.test_controls.stats())

    @app.get("/_test/received")
    async def received(request: Request) -> JSONResponse:
        guard(request)
        items = [{"path": r.path, "member_id": r.member_id, "body": r.body} for r in controls.received()]
        return JSONResponse({"requests": items})

    @app.post("/_test/fail-next")
    async def fail_next(request: Request) -> JSONResponse:
        guard(request)
        document = parse_json_request(await request.body(), request.headers.get("content-type"))
        spec = parse_failure_spec(document, contract_routes(app), allow_after_commit=True)
        controls.arm(spec)
        return JSONResponse(
            {"armed": spec.count, "status": spec.status, "when": spec.when.value, "path": spec.path}
        )

    @app.get("/_test/dump")
    async def dump(request: Request) -> JSONResponse:
        guard(request)
        return JSONResponse(double.test_controls.dump())

    @app.post("/_test/tamper")
    async def tamper(request: Request) -> JSONResponse:
        guard(request)
        return JSONResponse(
            double.test_controls.tamper(
                parse_tamper_request(
                    parse_json_request(await request.body(), request.headers.get("content-type"))
                )
            )
        )

    @app.post("/_test/checkpoint")
    async def checkpoint(request: Request) -> JSONResponse:
        guard(request)
        return JSONResponse(double.test_controls.checkpoint_now())
