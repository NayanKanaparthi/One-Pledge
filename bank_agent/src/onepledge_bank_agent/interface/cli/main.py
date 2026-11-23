"""`onepledge-bank`: database administration, reference seeding, the pipeline worker, registry sync and
integrity (SPEC 19).

Exit codes: 0 on success, 1 on failure (one sentence on stderr, never a traceback
unless `--debug` is given), 2 on a usage error. With `--json` stdout holds exactly one
JSON document, also on failure and on a usage error (SPEC_DECISIONS 20, 78). Log lines
go to stderr; the last resort handler writes one `unhandled_exception` error record with
the exception type and its frames before its sentence, for every command.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.domain.dependency_errors import RegistryIntegrityBrokenError
from onepledge_bank_agent.domain.errors import MESSAGES, DomainError, PipelineStageError, ReferenceDataError
from onepledge_bank_agent.infrastructure.logging import StructlogOperationalLog, configure_logging
from onepledge_bank_agent.infrastructure.operator_errors import OperatorError
from onepledge_bank_agent.interface.cli.commands import run_command
from onepledge_bank_agent.interface.cli.output import USAGE_STATUS, Output
from onepledge_bank_agent.interface.cli.parser import ProductParser
from onepledge_bank_agent.interface.composition import Collaborators

UNEXPECTED = "Unexpected error. Run with --debug for details."
UNEXPECTED_CODE = "INTERNAL_ERROR"
DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
DEPENDENCY_REJECTED = "DEPENDENCY_REJECTED"


def _common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print the result as one JSON document")
    parser.add_argument("--quiet", action="store_true", help="log only warnings and errors")
    parser.add_argument("--debug", action="store_true", help="print the exception type and call frames")
    parser.add_argument(
        "--wide", action="store_true", help="do not wrap human readable output at 100 columns"
    )


def build_parser() -> argparse.ArgumentParser:
    """The argument parser with every command this package provides so far."""
    parser = ProductParser(
        prog="onepledge-bank",
        description="OnePledge bank agent: the program that runs inside one bank.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    db = commands.add_parser(
        "db",
        help="create, migrate or inspect the bank database",
        description="Database administration.",
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
        description="Advance tape submissions through the pipeline as the app role (DATABASE_URL) and "
        "sync the registry outbox. It calls outside records (OUTSIDE_RECORDS_URL, OUTSIDE_RECORDS_API_KEY), "
        "key holder 1 (KEY_HOLDER_1_URL, KEY_HOLDER_1_API_KEY), key holder 2 (KEY_HOLDER_2_URL, "
        "KEY_HOLDER_2_API_KEY) and the registry (REGISTRY_URL, REGISTRY_API_KEY, REGISTRY_MEMBER_ID, "
        "REGISTRY_PUBLIC_KEYS). Stops on SIGTERM or SIGINT.",
    )
    worker.add_argument(
        "--once", action="store_true", help="process until no submission is runnable, print what ran, exit"
    )
    registry_side = _registry_parsers(commands)
    for sub in (provision, migrate, status, seed, worker, *registry_side):
        _common_flags(sub)
    return parser


def _registry_parsers(
    commands: argparse._SubParsersAction[ProductParser],
) -> list[argparse.ArgumentParser]:
    """`registry sync`, `integrity report` and `integrity verify`."""
    registry = commands.add_parser(
        "registry",
        help="talk to the shared registry",
        description="Registry side actions.",
    )
    registry_actions = registry.add_subparsers(dest="registry_command", required=True, metavar="ACTION")
    sync = registry_actions.add_parser(
        "sync",
        help="pull and process the pending registry messages now (alerts, case updates, checkpoints)",
        description="Read this bank's registry outbox, process every message and acknowledge it, as the "
        "app role. Audited as REGISTRY_SYNC_REQUESTED with via cli.",
    )
    integrity = commands.add_parser(
        "integrity",
        help="show or recompute what this bank has verified",
        description="Integrity checks.",
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


def wants_json(argv: Sequence[str]) -> bool:
    """A usage error is reported as JSON when any argument starts with `--json` (SPEC_DECISIONS 121.3)."""
    return any(argument.startswith("--json") for argument in argv)


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the arguments; `worker --json` without `--once` is a usage error (it prints no result)."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "worker" and arguments.json and not arguments.once:
        parser.error("argument --json: must be given with --once, the worker without it prints no result")
    return arguments


def _sentence(error: DomainError) -> tuple[str, str]:
    """The code and the one sentence of a business error on the command line."""
    if isinstance(error, ReferenceDataError):
        return error.code, error.sentence
    if isinstance(error, RegistryIntegrityBrokenError):
        return error.code, MESSAGES[error.code]
    if isinstance(error, PipelineStageError):
        code = DEPENDENCY_UNAVAILABLE if error.transient else DEPENDENCY_REJECTED
        return code, MESSAGES[code]
    return error.code, error.message


def main(
    argv: Sequence[str] | None = None,
    output: Output | None = None,
    collaborators: Collaborators | None = None,
) -> int:
    """Entry point."""
    out = output if output is not None else Output()
    given = list(sys.argv[1:] if argv is None else argv)
    try:
        arguments = parse(given)
    except SystemExit as exit_:
        if exit_.code == USAGE_STATUS and wants_json(given):
            out.usage_failure()
        raise
    out.as_json, out.wide = arguments.json, arguments.wide
    configure_logging(None, "ERROR", sys.stderr)  # until the command routes its logs with its bank code
    try:
        return run_command(arguments, out, collaborators)
    except OperatorError as error:
        return out.failure(error.code, error.sentence)
    except DomainError as error:
        return out.failure(*_sentence(error))
    except Exception as error:  # the last resort of SPEC_DECISIONS 23 (d): one record, one sentence
        if arguments.debug:
            out.detail(frames_only(error))
        StructlogOperationalLog(__name__).failure(
            LogEvent.UNHANDLED_EXCEPTION, error, error_code=UNEXPECTED_CODE
        )
        return out.failure(UNEXPECTED_CODE, UNEXPECTED)


def frames_only(error: BaseException) -> str:
    """The exception type and its frames (file, line, function): no message, no source line, no locals
    (SPEC_DECISIONS 53.12), so the text is safe on any stream."""
    frames = traceback.extract_tb(error.__traceback__)
    lines = ["Traceback, frames only (most recent call last):"]
    lines += [f"  {frame.filename}:{frame.lineno} {frame.name}" for frame in frames]
    lines.append(type(error).__name__)
    return "\n".join(lines) + "\n"
