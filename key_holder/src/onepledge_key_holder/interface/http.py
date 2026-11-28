"""The FastAPI application of the key holder (`contracts/key_holder.yaml`).

WP1 serves `GET /healthz` only. Its body is exactly the contract's `Health` schema, without
`test_double` (SPEC_DECISIONS 11). The app never redirects (a trailing slash is not
rewritten) and publishes no generated documentation routes.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from onepledge_key_holder.application.report_health import ReportHealth

SERVICE = "key_holder"


def create_app(report_health: ReportHealth) -> FastAPI:
    """The key holder app, answering health from `report_health`."""
    app = FastAPI(
        title="OnePledge key holder",
        redirect_slashes=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        report = report_health.run()
        return JSONResponse(
            {
                "status": "ok",
                "service": SERVICE,
                "holder_id": report.holder_id.value,
                "active_key_version": report.active_key_version.number,
            }
        )

    return app
