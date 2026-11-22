"""Running a double's FastAPI app with uvicorn (TEST DOUBLES).

One worker, uvicorn's own access log off, logging through `logging_setup`.
"""

from __future__ import annotations

import errno
import ipaddress
import socket

import uvicorn
from fastapi import FastAPI

from onepledge_contract_fakes.logging_setup import LogEvent, configure_logging, get_logger

IPV6 = 6


class BindError(Exception):
    """The configured host and port cannot be bound; carries the operator sentence."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


def ensure_port_free(host: str, port: int) -> None:
    """Fail early with one sentence when the address cannot be bound (taken, not permitted, not local)."""
    family = socket.AF_INET6 if ipaddress.ip_address(host).version == IPV6 else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as error:
            if error.errno == errno.EADDRNOTAVAIL:
                raise BindError(
                    f"BIND_HOST {host} is not an address of this machine; "
                    "set BIND_HOST to 127.0.0.1 or 0.0.0.0."
                ) from None
            if error.errno in (errno.EADDRINUSE, errno.EACCES):
                raise BindError(
                    f"Port {port} on {host} is already in use or not permitted; set another PORT."
                ) from None
            raise


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
    """Serve the app until interrupted; log lines go to stdout."""
    ensure_port_free(host, port)
    configure_logging(service, log_level)
    get_logger(__name__).info(LogEvent.SERVICE_STARTED)
    build_server(app, host, port).run()
