"""`onepledge-keyholder`: the command line of the key holder (SPEC section 3).

`db provision` creates this holder's database and its two roles. `db migrate` runs the
migrations as the owner role and records the active key version and the member key hashes.
`serve` serves the API as the app role. A setting that is missing or malformed, or a
database that is not ready, is one sentence on stderr and exit 1; a usage error is exit 2.
No sentence repeats a value.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import uvicorn
from fastapi import FastAPI

from onepledge_key_holder.infrastructure.db.engines import DatabaseTarget, OperatorError
from onepledge_key_holder.infrastructure.db.migrations import MigrationRunner
from onepledge_key_holder.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_key_holder.infrastructure.db.registration import ConfigurationRegistrar
from onepledge_key_holder.infrastructure.db.repositories import Database
from onepledge_key_holder.infrastructure.logging_setup import SERVICE_STARTED, configure_logging, get_logger
from onepledge_key_holder.infrastructure.settings import (
    ConfigurationError,
    load_migrate_settings,
    load_provision_settings,
    load_serve_settings,
)
from onepledge_key_holder.infrastructure.system import SystemClock
from onepledge_key_holder.interface.composition import build_app
from onepledge_key_holder.interface.http import SERVICE


def build_parser() -> argparse.ArgumentParser:
    """The argument parser: `db provision`, `db migrate` and `serve`."""
    parser = argparse.ArgumentParser(
        prog="onepledge-keyholder",
        description="OnePledge key holder: evaluates blinded elements with this holder's scalar.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    db = commands.add_parser(
        "db",
        help="create or migrate this holder's database",
        description="Database administration.",
        allow_abbrev=False,
    )
    db_commands = db.add_subparsers(dest="db_command", required=True, metavar="ACTION")
    db_commands.add_parser(
        "provision",
        help="create the roles and the database (needs PG_ADMIN_URL), idempotent",
        description="Create {db}_owner, {db}_app and the database named in DATABASE_URL and "
        "MIGRATION_DATABASE_URL, using the superuser PG_ADMIN_URL. Safe to repeat.",
        allow_abbrev=False,
    )
    db_commands.add_parser(
        "migrate",
        help="run the migrations as the owner role and record the key version and member key hashes",
        description="Run Alembic to head with MIGRATION_DATABASE_URL, then record ACTIVE_KEY_VERSION "
        "and MEMBER_KEY_HASHES. Safe to repeat.",
        allow_abbrev=False,
    )
    commands.add_parser(
        "serve",
        help="serve the API on BIND_HOST:PORT",
        description="Serve the key holder API as the app role (HOLDER_ID, ACTIVE_KEY_VERSION, "
        "OPRF_SCALAR_B64, DATABASE_URL, DAILY_ELEMENT_CAP, PORT, BIND_HOST).",
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


def _provision() -> None:
    settings = load_provision_settings()
    target = DatabaseTarget.from_urls(settings.migration_url, settings.database_url)
    DatabaseProvisioner(settings.admin_url).provision(target)
    sys.stdout.write(
        f"Provisioned database {target.database} with roles {target.owner_role} and {target.app_role}.\n"
    )


def _migrate() -> None:
    settings = load_migrate_settings()
    revision = MigrationRunner(settings.migration_url).upgrade()
    registration = ConfigurationRegistrar(settings.migration_url).register(
        settings.active_key_version, settings.member_key_hashes, SystemClock().now()
    )
    sys.stdout.write(
        f"Migrated the key holder database to revision {revision}. "
        f"Key version {registration.active_key_version.number} is active. "
        f"Active member credentials: {registration.active_credentials}.\n"
    )


def _serve() -> None:
    settings = load_serve_settings()
    database = Database(settings.database_url)
    try:
        database.require_active(settings.active_key_version)
        configure_logging()
        app = build_app(settings, database)
        get_logger(SERVICE, settings.holder_id.value).info(SERVICE_STARTED)
        build_server(app, settings.bind_host, settings.port).run()
    finally:
        database.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Exit 0 on success, 1 on a configuration or database error, 2 on a usage error."""
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "serve":
            _serve()
        elif arguments.db_command == "provision":
            _provision()
        else:
            _migrate()
    except (ConfigurationError, OperatorError) as error:
        sys.stderr.write(error.sentence + "\n")
        return 1
    return 0
