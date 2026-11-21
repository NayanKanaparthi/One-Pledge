"""`onepledge-outside`: provision, migrate, seed, register clients and serve (SPEC 6.3)."""

from __future__ import annotations

import argparse
import errno
import socket
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

import uvicorn

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.logging_setup import LogEvent, configure_logging, get_logger
from onepledge_outside_records.infrastructure.settings import Settings, load_settings, read_secret_variable
from onepledge_outside_records.interface.composition import Composition

SERVICE_NAME = "outside-records"


class Console:
    """Writes command results to stdout and one sentence errors to stderr."""

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr

    def line(self, text: str) -> None:
        """One line of output."""
        self._out.write(text + "\n")

    def error(self, sentence: str) -> None:
        """One sentence on stderr."""
        self._err.write(sentence + "\n")


def build_parser() -> argparse.ArgumentParser:
    """Argument parser with the commands of SPEC 6.3."""
    parser = argparse.ArgumentParser(
        prog="onepledge-outside",
        description="Outside records: read-only title, payment and contract lookups for bank agents.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    db = commands.add_parser(
        "db", help="create or migrate the database", description="Database administration."
    )
    db_commands = db.add_subparsers(dest="db_command", required=True, metavar="ACTION")
    db_commands.add_parser(
        "provision",
        help="create the roles and the database (needs PG_ADMIN_URL), idempotent",
        description="Create {db}_owner and {db}_app and the database named in DATABASE_URL and "
        "MIGRATION_DATABASE_URL, using the superuser PG_ADMIN_URL. Safe to repeat.",
    )
    db_commands.add_parser(
        "migrate",
        help="run the migrations to head as the owner role",
        description="Run Alembic to head with MIGRATION_DATABASE_URL; grants go to DB_APP_ROLE.",
    )
    seed = commands.add_parser(
        "seed",
        help="replace the records from a data directory in one transaction",
        description="Replace title_records, payments and contracts from DIR/outside_records/*.csv "
        "as the owner role.",
    )
    seed.add_argument(
        "--data-dir", type=Path, required=True, metavar="DIR", help="a directory written by onepledge-datagen"
    )
    clients = commands.add_parser(
        "clients", help="manage API clients", description="API client administration."
    )
    client_commands = clients.add_subparsers(dest="clients_command", required=True, metavar="ACTION")
    add = client_commands.add_parser(
        "add",
        help="add a client or replace its key",
        description="Store the SHA-256 of the key read from an environment variable; "
        "the key itself is never stored.",
    )
    add.add_argument("--name", required=True, metavar="NAME", help="client name, for example harbor")
    add.add_argument(
        "--key-env", required=True, metavar="VAR", help="name of the environment variable that holds the key"
    )
    commands.add_parser(
        "serve",
        help="serve the API on BIND_HOST:PORT",
        description="Serve the API as the read-only app role (DATABASE_URL, PORT, BIND_HOST, LOG_LEVEL).",
    )
    return parser


def _port_free(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError as error:
            if error.errno in (errno.EADDRINUSE, errno.EACCES, errno.EADDRNOTAVAIL):
                raise OperatorError(
                    f"Port {port} on {host} is already in use or not available; set another PORT."
                ) from None
            raise


def _serve(settings: Settings, composition: Composition) -> None:
    port = settings.require_port()
    _port_free(settings.bind_host, port)
    app = composition.service()
    configure_logging(SERVICE_NAME, settings.log_level)
    get_logger(__name__).info(LogEvent.SERVICE_STARTED)
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=port,
        workers=1,
        access_log=False,
        log_config=None,
        log_level="warning",
        server_header=False,
        date_header=False,
    )


def run(arguments: argparse.Namespace, settings: Settings, console: Console) -> int:
    """Run one parsed command."""
    with Composition(settings) as composition:
        _dispatch(arguments, settings, composition, console)
    return 0


def _dispatch(
    arguments: argparse.Namespace, settings: Settings, composition: Composition, console: Console
) -> None:
    if arguments.command == "db" and arguments.db_command == "provision":
        provisioner, target = composition.provisioner()
        provisioner.provision(target)
        console.line(
            f"Provisioned database {target.database} with roles {target.owner_role} and {target.app_role}."
        )
    elif arguments.command == "db":
        composition.migrations().upgrade()
        console.line("Migrated the outside records database to the latest revision.")
    elif arguments.command == "seed":
        counts = composition.seed_records().run(arguments.data_dir)
        console.line("Seeded outside records:")
        for table, count in (
            ("title_records", counts.titles),
            ("payments", counts.payments),
            ("contracts", counts.contracts),
        ):
            console.line(f"  {table:<14} {count:>9,} rows")
    elif arguments.command == "clients":
        client = composition.register_client().run(arguments.name, read_secret_variable(arguments.key_env))
        console.line(f"Client {client.name} is active (client id {client.client_id}).")
    else:
        _serve(settings, composition)


def main(argv: Sequence[str] | None = None, console: Console | None = None) -> int:
    """Entry point. Exit 0 on success, 1 on failure, 2 on a usage error."""
    out = console or Console()
    arguments = build_parser().parse_args(argv)
    try:
        return run(arguments, load_settings(), out)
    except OperatorError as error:
        out.error(error.sentence)
        return 1
