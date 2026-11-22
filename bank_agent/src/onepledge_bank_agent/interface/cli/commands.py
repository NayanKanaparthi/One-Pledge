"""The commands of `onepledge-bank`: `db provision|migrate|status`, `seed-reference`, `worker`,
`registry sync` and `integrity report|verify`.

Each command loads the settings of its own scope, routes its log lines to stderr,
calls collaborators built by the matching composition root, and prints the result
through `Output`. The administrative commands never build runtime settings and the
worker never builds administrative ones (SPEC_DECISIONS 22, finding 5).
"""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Callable, Mapping
from types import FrameType, MappingProxyType

from onepledge_bank_agent.application.advance_submission import StageRun
from onepledge_bank_agent.application.run_pipeline_worker import WorkerReport
from onepledge_bank_agent.application.seed_reference import UpsertCounts
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.infrastructure.db.errors import database_errors_as_operator_errors
from onepledge_bank_agent.infrastructure.logging import configure_logging
from onepledge_bank_agent.infrastructure.settings import (
    AdminSettings,
    load_admin_settings,
    load_runtime_settings,
)
from onepledge_bank_agent.interface.cli.output import Output, short_hash
from onepledge_bank_agent.interface.cli.registry_commands import (
    integrity_report,
    integrity_verify,
    registry_sync,
)
from onepledge_bank_agent.interface.composition import AdminComposition, Collaborators, RuntimeComposition

QUIET_LEVEL = "WARNING"
WORKER_PROCESS = "worker"
type AdminHandler = Callable[[argparse.Namespace, AdminSettings, AdminComposition, Output], None]
type RuntimeHandler = Callable[[argparse.Namespace, RuntimeComposition, Output], int]
RUNTIME_HANDLERS: Mapping[str, RuntimeHandler] = MappingProxyType(
    {"sync": registry_sync, "report": integrity_report, "verify": integrity_verify}
)


def _route_logs(bank_code: str | None, level: str, quiet: bool) -> None:
    """JSON log lines on stderr; stdout stays the result's alone (SPEC_DECISIONS 27)."""
    configure_logging(bank_code, QUIET_LEVEL if quiet else level, sys.stderr)


def run_command(arguments: argparse.Namespace, output: Output, collaborators: Collaborators | None) -> int:
    """Dispatch one parsed command; returns the exit code."""
    if arguments.command == "worker":
        return _worker(arguments, output, collaborators)
    if arguments.command in ("registry", "integrity"):
        return _runtime_command(arguments, output, collaborators)
    handlers: dict[str, AdminHandler] = {
        "provision": _provision,
        "migrate": _migrate,
        "status": _status,
        "seed-reference": _seed_reference,
    }
    handler = handlers[arguments.db_command if arguments.command == "db" else arguments.command]
    settings = load_admin_settings()
    _route_logs(settings.bank_code, settings.log_level, arguments.quiet)
    with database_errors_as_operator_errors(), AdminComposition(settings, collaborators) as composition:
        handler(arguments, settings, composition, output)
    return 0


def _provision(
    arguments: argparse.Namespace, settings: AdminSettings, composition: AdminComposition, output: Output
) -> None:
    provisioner, target = composition.provisioner()
    provisioner.provision(target)
    if output.as_json:
        output.success(
            {"database": target.database, "owner_role": target.owner_role, "app_role": target.app_role}
        )
        return
    output.line(
        f"Provisioned database {target.database} with roles {target.owner_role} and {target.app_role}."
    )


def _migrate(
    arguments: argparse.Namespace, settings: AdminSettings, composition: AdminComposition, output: Output
) -> None:
    composition.owner_guard().require_owner()
    runner = composition.migrations()
    before = runner.current_revision()
    applied = runner.upgrade()
    after = runner.current_revision()
    if after is None:
        after = before or ""
    changed = composition.record_schema_migration().run(before, after, applied)
    if output.as_json:
        output.success({"from_revision": before, "to_revision": after, "changed": changed})
        return
    if changed:
        output.line(f"Migrated the bank database from revision {before or 'none'} to {after}.")
    else:
        output.line(f"The bank database is already at revision {after}.")


def _status(
    arguments: argparse.Namespace, settings: AdminSettings, composition: AdminComposition, output: Output
) -> None:
    status = composition.schema().status()
    if output.as_json:
        output.success(
            {
                "database": status.database,
                "current_revision": status.current_revision,
                "head_revision": status.head_revision,
                "at_head": status.at_head,
            }
        )
        return
    output.pairs(
        "Bank database schema",
        {
            "database": status.database,
            "current revision": status.current_revision or "none (not migrated)",
            "latest revision": status.head_revision,
            "at latest": "yes" if status.at_head else "no, run db migrate",
        },
    )


def _counts_row(table: str, counts: UpsertCounts) -> tuple[str, str, str, str]:
    return table, f"{counts.inserted:,}", f"{counts.updated:,}", f"{counts.unchanged:,}"


def _seed_reference(
    arguments: argparse.Namespace, settings: AdminSettings, composition: AdminComposition, output: Output
) -> None:
    bank_code = settings.require_bank_code()
    composition.owner_guard().require_owner()
    result = composition.seed_reference().run(arguments.data_dir, bank_code)
    if output.as_json:
        output.success(
            {
                "bank_code": bank_code,
                "source_sha256": result.source_sha256,
                "lenders": result.lenders.as_detail(),
                "facilities": result.facilities.as_detail(),
            }
        )
        return
    output.table(
        f"Reference data of {bank_code}",
        ("table", "inserted", "updated", "unchanged"),
        (_counts_row("lenders", result.lenders), _counts_row("facilities", result.facilities)),
        ("table seeded", "tables seeded"),
        f"Lenders and facilities are up to date (reference.json {short_hash(result.source_sha256)}).",
    )


FAILURE_KEYS = ("stage", "failure_code", "attempts", "dependency", "http_status", "reason")


def _run_document(run: StageRun) -> dict[str, JsonValue]:
    """One stage run with the six failure facts of SPEC_DECISIONS 58 (null when it advanced)."""
    failure = run.failure or {}
    return {
        "submission_id": str(run.submission_id),
        "from": run.started_at_status.value,
        "outcome": run.outcome.value,
        "status": run.status.value,
        **{key: failure.get(key) for key in FAILURE_KEYS},
    }


def _run_row(run: StageRun) -> tuple[str, ...]:
    document = _run_document(run)
    return tuple("" if document[key] is None else str(document[key]) for key in document)


TASK_FAILED = (
    "The registry outbox sync failed; every runnable stage ran. The log names the cause; "
    "run registry sync again."
)


def _report(report: WorkerReport, output: Output) -> int:
    """The stage runs; exit 1 after them when a periodic task failed (SPEC_DECISIONS 85, rule 5)."""
    failed = bool(report.task_failures)
    stages: list[JsonValue] = [_run_document(run) for run in report.runs]
    if output.as_json:
        error: dict[str, JsonValue] = (
            {"code": report.task_failures[0], "message": TASK_FAILED} if failed else {}
        )
        output.json({"ok": not failed, "stages": stages, **({"error": error} if failed else {})})
    else:
        output.table(
            "Pipeline worker",
            ("submission", "from", "outcome", "status", *FAILURE_KEYS),
            [_run_row(run) for run in report.runs],
            ("stage run", "stages run"),
            "Nothing else is runnable now.",
        )
    if failed:
        output.error(TASK_FAILED)
    return 1 if failed else 0


def _worker(arguments: argparse.Namespace, output: Output, collaborators: Collaborators | None) -> int:
    settings = load_runtime_settings()
    _route_logs(settings.require_bank_code(), settings.log_level, arguments.quiet)
    with database_errors_as_operator_errors(), RuntimeComposition(settings, collaborators) as composition:
        composition.schema().require_head()
        composition.role_guard().require_least_privilege()
        composition.startup_record().run(WORKER_PROCESS)
        worker, stop = composition.worker(), composition.stop_signal()

        def request_stop(number: int, frame: FrameType | None) -> None:
            stop.request_stop()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        if arguments.once:
            return _report(worker.run_once(), output)
        worker.run_until_stopped()
        return 0


UNGUARDED = frozenset({"report"})


def _runtime_command(
    arguments: argparse.Namespace, output: Output, collaborators: Collaborators | None
) -> int:
    """`registry sync`, `integrity report`, `integrity verify`: the app role, the runtime settings.
    `integrity report` skips the schema and role checks, so it can show the controls that are false."""
    settings = load_runtime_settings()
    _route_logs(settings.bank_code, settings.log_level, arguments.quiet)
    action = arguments.registry_command if arguments.command == "registry" else arguments.integrity_command
    with database_errors_as_operator_errors(), RuntimeComposition(settings, collaborators) as composition:
        if action not in UNGUARDED:  # SPEC_DECISIONS 72, 93.8: integrity report must show a false control
            composition.schema().require_head()
            composition.role_guard().require_least_privilege()
        return RUNTIME_HANDLERS[action](arguments, composition, output)
