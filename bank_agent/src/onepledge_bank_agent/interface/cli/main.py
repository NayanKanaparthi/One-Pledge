"""`onepledge-bank`: database administration, reference seeding, the pipeline worker, registry sync and
integrity (SPEC 19).

Exit codes: 0 on success, 1 on failure (one sentence on stderr, never a traceback
unless `--debug` is given), 2 on a usage error. With `--json` stdout holds exactly one
JSON document, also on failure (SPEC_DECISIONS 20). Log lines go to stderr.
"""

from __future__ import annotations

import argparse
import traceback
from collections.abc import Sequence
from pathlib import Path

from onepledge_bank_agent.domain.errors import MESSAGES, DomainError, PipelineStageError, ReferenceDataError
from onepledge_bank_agent.infrastructure.operator_errors import OperatorError
from onepledge_bank_agent.interface.cli.commands import run_command
from onepledge_bank_agent.interface.cli.output import Output
from onepledge_bank_agent.interface.composition import Collaborators

UNEXPECTED = "Unexpected error. Run with --debug for details."
UNEXPECTED_CODE = "INTERNAL_ERROR"
DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
DEPENDENCY_REFUSED = "DEPENDENCY_REFUSED"
REFUSED = "A service this command depends on refused the request; check its URL and key, then run it again."


def _common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print the result as one JSON document")
    parser.add_argument("--quiet", action="store_true", help="log only warnings and errors")
    parser.add_argument("--debug", action="store_true", help="print the traceback of an unexpected error")


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
        description="Upsert the lenders and facilities of BANK_CODE from the seed file DIR/reference.json "
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
        "--once", action="store_true", help="process until no submission is runnable, print what ran, exit"
    )
    registry_side = _registry_parsers(commands)
    for sub in (provision, migrate, status, seed, worker, *registry_side):
        _common_flags(sub)
    return parser


def _registry_parsers(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> list[argparse.ArgumentParser]:
    """`registry sync`, `integrity report` and `integrity verify`."""
    registry = commands.add_parser(
        "registry", help="talk to the shared registry", description="Registry side actions."
    )
    registry_actions = registry.add_subparsers(dest="registry_command", required=True, metavar="ACTION")
    sync = registry_actions.add_parser(
        "sync",
        help="pull and process the pending registry messages now (alerts, case updates, checkpoints)",
        description="Read this bank's registry outbox, process every message and acknowledge it, as the "
        "app role. Audited as REGISTRY_SYNC_REQUESTED with via cli.",
    )
    integrity = commands.add_parser(
        "integrity", help="show or recompute what this bank has verified", description="Integrity checks."
    )
    integrity_actions = integrity.add_subparsers(dest="integrity_command", required=True, metavar="ACTION")
    report = integrity_actions.add_parser(
        "report",
        help="show the live integrity report",
        description="Checkpoints and certificates received, the audit chain and raw rows as of the last deep "
        "verification, and the database controls in force now.",
    )
    verify = integrity_actions.add_parser(
        "verify",
        help="recompute every proof from stored data, store and show the report",
        description="Walk the audit chain, recompute every raw row and file hash, re-verify every "
        "certificate and checkpoint signature and the registry chain from seq 1. Needs the registry.",
    )
    return [sync, report, verify]


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the arguments; `worker --json` without `--once` is a usage error (it prints no result)."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "worker" and arguments.json and not arguments.once:
        parser.error("--json needs --once: the worker without --once runs until stopped and prints no result")
    return arguments


def _sentence(error: DomainError) -> tuple[str, str]:
    """The code and the one sentence of a business error on the command line."""
    if isinstance(error, ReferenceDataError):
        return error.code, error.sentence
    if isinstance(error, PipelineStageError):
        if error.transient:
            return DEPENDENCY_UNAVAILABLE, MESSAGES[DEPENDENCY_UNAVAILABLE]
        return DEPENDENCY_REFUSED, REFUSED
    return error.code, error.message


def main(
    argv: Sequence[str] | None = None,
    output: Output | None = None,
    collaborators: Collaborators | None = None,
) -> int:
    """Entry point."""
    arguments = parse(argv)
    out = output if output is not None else Output()
    out.as_json = arguments.json
    try:
        return run_command(arguments, out, collaborators)
    except OperatorError as error:
        return out.failure(error.code, error.sentence)
    except DomainError as error:
        return out.failure(*_sentence(error))
    except Exception:  # the last resort of SPEC_DECISIONS 23 (d): one sentence, the traceback only on request
        if arguments.debug:
            out.detail(traceback.format_exc())
        return out.failure(UNEXPECTED_CODE, UNEXPECTED)
