"""Running a double's FastAPI app with uvicorn (TEST DOUBLES).

One worker, uvicorn's own access log off, logging through `logging_setup`.
"""

from __future__ import annotations

import errno
import socket

import uvicorn
from fastapi import FastAPI

from onepledge_contract_fakes.logging_setup import LogEvent, configure_logging, get_logger


class PortUnavailableError(Exception):
    """The configured host and port cannot be bound."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__("port unavailable")
        self.host = host
        self.port = port


def ensure_port_free(host: str, port: int) -> None:
    """Fail early with a clear error when the port is already taken."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as error:
            if error.errno in (errno.EADDRINUSE, errno.EACCES, errno.EADDRNOTAVAIL):
                raise PortUnavailableError(host, port) from None
            raise


def run_server(app: FastAPI, service: str, host: str, port: int, log_level: str) -> None:
    """Serve the app until interrupted."""
    ensure_port_free(host, port)
    configure_logging(service, log_level)
    get_logger(__name__).info(LogEvent.SERVICE_STARTED)
    uvicorn.run(
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
