"""`onepledge-bank`: database administration, reference seeding and the pipeline worker (SPEC 19).

Exit codes: 0 on success, 1 on failure (one sentence on stderr, never a traceback),
2 on a usage error.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from onepledge_bank_agent.domain.errors import DomainError, ReferenceDataError
from onepledge_bank_agent.infrastructure.db.errors import database_errors_as_operator_errors
from onepledge_bank_agent.infrastructure.operator_errors import OperatorError
from onepledge_bank_agent.infrastructure.settings import Settings, load_settings
from onepledge_bank_agent.interface.cli.commands import run_command
from onepledge_bank_agent.interface.cli.output import Output
from onepledge_bank_agent.interface.composition import Collaborators, Composition


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print the result as JSON")


def build_parser() -> argparse.ArgumentParser:
    """The argument parser with every command this package provides so far."""
    parser = argparse.ArgumentParser(
        prog="onepledge-bank",
        description="OnePledge bank agent: the program that runs inside one bank.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    db = commands.add_parser(
        "db", help="create, migrate or inspect the bank database", description="Database administration."
    )
    actions = db.add_subparsers(dest="db_command", required=True, metavar="ACTION")
    provision = actions.add_parser(
        "provision",
        help="create the two roles and the database (needs PG_ADMIN_URL), idempotent",
        description="Create {db}_owner and {db}_app and the database named in DATABASE_URL and "
        "MIGRATION_DATABASE_URL, using the superuser PG_ADMIN_URL. Safe to repeat.",
    )
    migrate = actions.add_parser(
        "migrate",
        help="run the migrations to the latest revision as the owner role",
        description="Run Alembic to head with MIGRATION_DATABASE_URL; grants go to DB_APP_ROLE.",
    )
    status = actions.add_parser(
        "status",
        help="show the current and the latest schema revision",
        description="Read the schema revision as the app role (DATABASE_URL).",
    )
    seed = commands.add_parser(
        "seed-reference",
        help="load this bank's lenders and facilities, idempotent",
        description="Upsert the lenders and facilities of BANK_CODE from DIR/reference.json "
        "as the owner role (MIGRATION_DATABASE_URL).",
    )
    seed.add_argument(
        "--data-dir", type=Path, required=True, metavar="DIR", help="a directory written by onepledge-datagen"
    )
    worker = commands.add_parser(
        "worker",
        help="run the pipeline worker",
        description="Advance tape submissions through the pipeline as the app role (DATABASE_URL), "
        "calling outside records (OUTSIDE_RECORDS_URL, OUTSIDE_RECORDS_API_KEY). Stops on SIGTERM or SIGINT.",
    )
    worker.add_argument(
        "--once", action="store_true", help="process until no submission is runnable, then exit"
    )
    for sub in (provision, migrate, status, seed, worker):
        _json_flag(sub)
    return parser


def run(
    arguments: argparse.Namespace,
    settings: Settings,
    output: Output,
    collaborators: Collaborators | None = None,
) -> int:
    """Run one parsed command with its own composition."""
    with database_errors_as_operator_errors(), Composition(settings, collaborators) as composition:
        return run_command(arguments, settings, composition, output)


def main(
    argv: Sequence[str] | None = None,
    output: Output | None = None,
    collaborators: Collaborators | None = None,
) -> int:
    """Entry point."""
    arguments = build_parser().parse_args(argv)
    out = output if output is not None else Output(as_json=bool(getattr(arguments, "json", False)))
    out.as_json = bool(getattr(arguments, "json", False))
    try:
        return run(arguments, load_settings(), out, collaborators)
    except OperatorError as error:
        out.error(error.sentence)
    except ReferenceDataError as error:
        out.error(error.sentence)
    except DomainError as error:
        out.error(error.message)
    return 1
