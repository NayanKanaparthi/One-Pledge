"""`onepledge-keyholder`: the command line of the key holder (SPEC section 3).

WP1 has one command, `serve`. It reads its settings, builds the app and serves it with
uvicorn on BIND_HOST:PORT. A setting that is missing or malformed is one sentence on stderr
and exit 1; a usage error is exit 2.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import uvicorn
from fastapi import FastAPI

from onepledge_key_holder.application.report_health import ReportHealth
from onepledge_key_holder.infrastructure.settings import ConfigurationError, load_serve_settings
from onepledge_key_holder.interface.http import create_app


def build_parser() -> argparse.ArgumentParser:
    """The argument parser: one subcommand, `serve`."""
    parser = argparse.ArgumentParser(
        prog="onepledge-keyholder",
        description="OnePledge key holder: evaluates blinded elements with this holder's scalar.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser(
        "serve",
        help="serve the API on BIND_HOST:PORT",
        description="Serve the key holder API (HOLDER_ID, ACTIVE_KEY_VERSION, PORT, BIND_HOST).",
        allow_abbrev=False,
    )
    return parser


def build_server(app: FastAPI, host: str, port: int) -> uvicorn.Server:
    """A uvicorn server: one worker, no access log of its own, no Server or Date header."""
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


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Exit 0 after a clean shutdown, 1 on a configuration error, 2 on a usage error."""
    build_parser().parse_args(argv)
    try:
        settings = load_serve_settings()
    except ConfigurationError as error:
        sys.stderr.write(error.sentence + "\n")
        return 1
    app = create_app(ReportHealth(settings.holder_id, settings.active_key_version))
    build_server(app, settings.bind_host, settings.port).run()
    return 0
