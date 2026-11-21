"""Command line of the key holder double: `onepledge-fake-keyholder serve` (TEST DOUBLE)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from onepledge_contract_fakes.cli_output import CliOutput
from onepledge_contract_fakes.errors import ConfigurationError
from onepledge_contract_fakes.key_holder.app import create_key_holder_app
from onepledge_contract_fakes.key_holder.settings import load_settings
from onepledge_contract_fakes.server import PortUnavailableError, run_server


def build_parser() -> argparse.ArgumentParser:
    """Argument parser with one `serve` subcommand."""
    parser = argparse.ArgumentParser(
        prog="onepledge-fake-keyholder",
        description="Run the key holder TEST DOUBLE (contracts/key_holder.yaml). Not production code.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser(
        "serve",
        help="serve the key holder double over HTTP",
        description="Serve the key holder double on BIND_HOST:PORT. Configuration comes from the environment "
        "(HOLDER_ID, PORT, FAKE_KH_KEYS, FAKE_KH_ACTIVE_VERSION, FAKE_KH_MEMBERS, FAKE_KH_DAILY_ELEMENT_CAP, "
        "FAKE_TEST_CONTROLS, FAKE_TEST_CONTROL_KEY, BIND_HOST, LOG_LEVEL).",
    )
    return parser


def main(argv: Sequence[str] | None = None, output: CliOutput | None = None) -> int:
    """Entry point. Returns the process exit code."""
    out = output or CliOutput()
    build_parser().parse_args(argv)
    try:
        settings = load_settings()
        app = create_key_holder_app(settings.to_config())
        service = "keyholder-1-fake" if settings.holder_id == "kh1" else "keyholder-2-fake"
        run_server(app, service, settings.bind_host, settings.port, settings.log_level)
    except ConfigurationError as error:
        out.error(error.sentence())
        return 1
    except PortUnavailableError as error:
        out.error(f"Port {error.port} on {error.host} is already in use or not available; set another PORT.")
        return 1
    return 0
