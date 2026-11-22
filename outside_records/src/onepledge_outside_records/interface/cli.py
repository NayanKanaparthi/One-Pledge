"""`onepledge-outside`: provision, migrate, seed, register clients and serve (SPEC 6.3).

Every command but `serve` prints a result (text, or one JSON document with `--json`)
and writes its log lines to stderr; `serve` logs to stdout and prints nothing else.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.binding import check_binding
from onepledge_outside_records.infrastructure.logging_setup import LogEvent, configure_logging, get_logger
from onepledge_outside_records.infrastructure.settings import (
    AdminSettings,
    load_admin_settings,
    load_serve_settings,
    read_secret_variable,
)
from onepledge_outside_records.interface.composition import Composition
from onepledge_outside_records.interface.output import (
    Console,
    common_options,
    parse_arguments,
    plural,
    run_guarded,
)

# The `service` value in log lines is the contract's health value (SPEC_DECISIONS 22.6).
SERVICE_NAME = "outside_records"
QUIET_LEVEL = "WARNING"


def build_parser() -> argparse.ArgumentParser:
    """Argument parser with the commands of SPEC 6.3."""
    parser = argparse.ArgumentParser(
        prog="onepledge-outside",
        description="Outside records: read-only title, payment and contract lookups for bank agents.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    _add_db_commands(commands)
    _add_seed_command(commands)
    _add_clients_commands(commands)
    commands.add_parser(
        "serve",
        parents=[common_options(result=False, logs=True)],
        help="serve the API on BIND_HOST:PORT",
        description="Serve the API as the read-only app role (DATABASE_URL, PORT, BIND_HOST, LOG_LEVEL).",
    )
    return parser


type Commands = argparse._SubParsersAction[argparse.ArgumentParser]


def _add_db_commands(commands: Commands) -> None:
    db = commands.add_parser(
        "db", help="create or migrate the database", description="Database administration."
    )
    db_commands = db.add_subparsers(dest="db_command", required=True, metavar="ACTION")
    db_commands.add_parser(
        "provision",
        parents=[common_options(result=True, logs=True)],
        help="create the roles and the database (needs PG_ADMIN_URL), idempotent",
        description="Create {db}_owner and {db}_app and the database named in DATABASE_URL and "
        "MIGRATION_DATABASE_URL, using the superuser PG_ADMIN_URL. Safe to repeat.",
    )
    db_commands.add_parser(
        "migrate",
        parents=[common_options(result=True, logs=True)],
        help="run the migrations to head as the owner role",
        description="Run Alembic to head with MIGRATION_DATABASE_URL; grants go to DB_APP_ROLE.",
    )


def _add_seed_command(commands: Commands) -> None:
    seed = commands.add_parser(
        "seed",
        parents=[common_options(result=True, logs=True)],
        help="replace the records from a data directory in one transaction",
        description="Replace title_records, payments and contracts from DIR/outside_records/*.csv "
        "as the owner role.",
    )
    seed.add_argument(
        "--data-dir",
        type=non_empty_path,
        required=True,
        metavar="DIR",
        help="a directory written by onepledge-datagen",
    )


def _add_clients_commands(commands: Commands) -> None:
    clients = commands.add_parser(
        "clients", help="manage API clients", description="API client administration."
    )
    client_commands = clients.add_subparsers(dest="clients_command", required=True, metavar="ACTION")
    add = client_commands.add_parser(
        "add",
        parents=[common_options(result=True, logs=True)],
        help="add a client or replace its key",
        description="Store the SHA-256 of the key read from an environment variable; "
        "the key itself is never stored.",
    )
    add.add_argument(
        "--name",
        required=True,
        metavar="NAME",
        help="client name: 1 to 64 letters, digits, hyphens or underscores, for example harbor",
    )
    add.add_argument(
        "--key-env", required=True, metavar="VAR", help="name of the environment variable that holds the key"
    )


def non_empty_path(text: str) -> Path:
    """A directory argument: an empty value is a usage error, not the current directory."""
    if not text.strip():
        raise argparse.ArgumentTypeError("must name a directory, not be empty")
    return Path(text)


def build_server(app: FastAPI, host: str, port: int) -> uvicorn.Server:
    """A uvicorn server: one worker, uvicorn's access log off, logging left to `logging_setup`."""
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


def _serve(composition: Composition) -> None:
    settings = load_serve_settings()
    port = settings.require_port()
    check_binding(settings.bind_host, port)
    app = composition.service(settings)
    configure_logging(SERVICE_NAME, settings.log_level)
    get_logger(__name__).info(LogEvent.SERVICE_STARTED)
    build_server(app, settings.bind_host, port).run()


def _provision(arguments: argparse.Namespace, composition: Composition, console: Console) -> None:
    provisioner, target = composition.provisioner(_admin("provision", arguments, console))
    provisioner.provision(target)
    console.line(
        f"Provisioned database {target.database} with roles {target.owner_role} and {target.app_role}."
    )
    console.result(
        {"database": target.database, "owner_role": target.owner_role, "app_role": target.app_role}
    )


def _migrate(arguments: argparse.Namespace, composition: Composition, console: Console) -> None:
    runner = composition.migrations(_admin("migrate", arguments, console))
    runner.upgrade()
    revision = runner.head_revision()
    console.line(f"Migrated the outside records database to revision {revision}.")
    console.result({"revision": revision})


def _seed(arguments: argparse.Namespace, composition: Composition, console: Console) -> None:
    counts = composition.seed_records(_admin("seed", arguments, console)).run(arguments.data_dir)
    tables = {"title_records": counts.titles, "payments": counts.payments, "contracts": counts.contracts}
    console.line("Seeded outside records:")
    for table, count in tables.items():
        console.line(f"  {table:<14} {plural(count, 'row'):>14}")
    console.result({"tables": dict(tables)})


def _add_client(arguments: argparse.Namespace, composition: Composition, console: Console) -> None:
    settings = _admin("clients", arguments, console)
    client = composition.register_client(settings).run(
        arguments.name, read_secret_variable(arguments.key_env)
    )
    console.line(f"Client {client.name} is active (client id {client.client_id}).")
    console.result({"client": {"name": client.name, "client_id": client.client_id}})


def _admin(command: str, arguments: argparse.Namespace, console: Console) -> AdminSettings:
    """The command's settings; its log lines go to stderr so stdout holds only the result."""
    settings = load_admin_settings(command)
    configure_logging(SERVICE_NAME, QUIET_LEVEL if arguments.quiet else settings.log_level, console.err)
    return settings


def run(arguments: argparse.Namespace, console: Console) -> int:
    """Run one parsed command."""
    with Composition() as composition:
        if arguments.command == "db" and arguments.db_command == "provision":
            _provision(arguments, composition, console)
        elif arguments.command == "db":
            _migrate(arguments, composition, console)
        elif arguments.command == "seed":
            _seed(arguments, composition, console)
        elif arguments.command == "clients":
            _add_client(arguments, composition, console)
        else:
            _serve(composition)
    return 0


def main(argv: Sequence[str] | None = None, console: Console | None = None) -> int:
    """Entry point. Exit 0 on success, 1 on failure, 2 on a usage error."""
    out = console or Console()
    arguments = parse_arguments(build_parser(), argv, out)
    out.json_mode = bool(getattr(arguments, "json", False))
    out.wide = arguments.wide
    out.logs_to_stdout = arguments.command == "serve"
    return run_guarded(lambda: _run_reporting(arguments, out), out, debug=arguments.debug)


def _run_reporting(arguments: argparse.Namespace, console: Console) -> int:
    try:
        return run(arguments, console)
    except OperatorError as error:
        if arguments.debug and error.cause is not None:
            console.traceback(error.cause)
        console.failure(error.code, error.sentence)
        return 1
