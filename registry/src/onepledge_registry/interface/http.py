"""The FastAPI application of the registry (`contracts/registry.yaml`).

WP1 serves `GET /healthz` only. Its body is exactly the contract's `Health` schema, without
`test_double` (SPEC_DECISIONS 11). The app never redirects (a trailing slash is not
rewritten) and publishes no generated documentation routes.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

SERVICE = "registry"


def create_app() -> FastAPI:
    """The registry app."""
    app = FastAPI(
        title="OnePledge registry",
        redirect_slashes=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "service": SERVICE})

    return app
