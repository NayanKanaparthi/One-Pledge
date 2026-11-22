"""Running a double's FastAPI app with uvicorn (TEST DOUBLES).

One worker, uvicorn's own access log off, logging through `logging_setup`.
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from onepledge_contract_fakes.binding import check_binding
from onepledge_contract_fakes.logging_setup import LogEvent, configure_logging, get_logger


def build_server(app: FastAPI, host: str, port: int) -> uvicorn.Server:
    """A uvicorn server for the app: one worker, uvicorn's access log off, logging left to us."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        workers=1,
        access_log=False,
        log_config=None,
        log_level="warning",
        server_header=False,
        date_header=False,
    )
    return uvicorn.Server(config)


def run_server(app: FastAPI, service: str, host: str, port: int, log_level: str) -> None:
    """Serve the app until interrupted; log lines go to stdout. BIND_HOST and PORT are checked first."""
    check_binding(host, port)
    configure_logging(service, log_level)
    get_logger(__name__).info(LogEvent.SERVICE_STARTED)
    build_server(app, host, port).run()
