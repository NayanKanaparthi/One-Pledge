"""Command line of the key holder double: `onepledge-fake-keyholder serve` (TEST DOUBLE)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from onepledge_contract_fakes.binding import BindError
from onepledge_contract_fakes.cli_output import CliOutput, common_options, run_guarded
from onepledge_contract_fakes.errors import ConfigurationError
from onepledge_contract_fakes.key_holder.app import create_key_holder_app
from onepledge_contract_fakes.key_holder.settings import load_settings
from onepledge_contract_fakes.server import run_server

# The `service` value in log lines is the health value of the contract (SPEC_DECISIONS 22.6).
SERVICE_NAME = "key_holder"


def build_parser() -> argparse.ArgumentParser:
    """Argument parser with one `serve` subcommand."""
    parser = argparse.ArgumentParser(
        prog="onepledge-fake-keyholder",
        description="Run the key holder TEST DOUBLE (contracts/key_holder.yaml). Not production code.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser(
        "serve",
        parents=[common_options(result=False, logs=True)],
        help="serve the key holder double over HTTP",
        description="Serve the key holder double on BIND_HOST:PORT. Configuration comes from the environment "
        "(HOLDER_ID, PORT, FAKE_KH_KEYS, FAKE_KH_ACTIVE_VERSION, FAKE_KH_MEMBERS, FAKE_KH_DAILY_ELEMENT_CAP, "
        "FAKE_TEST_CONTROLS, FAKE_TEST_CONTROL_KEY, BIND_HOST, LOG_LEVEL).",
    )
    return parser


def main(argv: Sequence[str] | None = None, output: CliOutput | None = None) -> int:
    """Entry point. Returns the process exit code: 0, 1 on a failure, 2 on a usage error."""
    out = output or CliOutput()
    arguments = build_parser().parse_args(argv)
    return run_guarded(lambda: _serve(out), out, debug=arguments.debug)


def _serve(out: CliOutput) -> int:
    try:
        settings = load_settings()
        app = create_key_holder_app(settings.to_config())
        run_server(app, SERVICE_NAME, settings.bind_host, settings.port, settings.log_level)
    except ConfigurationError as error:
        out.error(error.sentence())
        return 1
    except BindError as error:
        out.error(error.sentence)
        return 1
    return 0
