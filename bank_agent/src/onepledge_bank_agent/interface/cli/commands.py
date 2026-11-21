"""The commands of `onepledge-bank`: `db provision|migrate|status`, `seed-reference` and `worker`.

Each command validates its arguments, calls one collaborator built by the
composition root, and prints the result through `Output`.
"""

from __future__ import annotations

import argparse
import signal
from types import FrameType

from onepledge_bank_agent.application.advance_submission import StageRun
from onepledge_bank_agent.application.run_pipeline_worker import WorkerReport
from onepledge_bank_agent.infrastructure.db.migrations import head_revision
from onepledge_bank_agent.infrastructure.logging import configure_logging
from onepledge_bank_agent.infrastructure.settings import Settings
from onepledge_bank_agent.infrastructure.stop_signal import EventStopSignal
from onepledge_bank_agent.interface.cli.output import Output
from onepledge_bank_agent.interface.composition import Composition


def run_command(
    arguments: argparse.Namespace, settings: Settings, composition: Composition, output: Output
) -> int:
    """Dispatch one parsed command; returns the exit code."""
    if arguments.command == "db":
        handlers = {"provision": _provision, "migrate": _migrate, "status": _status}
        handlers[arguments.db_command](composition, output)
    elif arguments.command == "seed-reference":
        _seed_reference(arguments, settings, composition, output)
    else:
        _worker(arguments, settings, composition, output)
    return 0


def _provision(composition: Composition, output: Output) -> None:
    provisioner, target = composition.provisioner()
    provisioner.provision(target)
    if output.as_json:
        output.json(
            {"database": target.database, "owner_role": target.owner_role, "app_role": target.app_role}
        )
        return
    output.line(
        f"Provisioned database {target.database} with roles {target.owner_role} and {target.app_role}."
    )


def _migrate(composition: Composition, output: Output) -> None:
    composition.migrations().upgrade()
    head = head_revision()
    if output.as_json:
        output.json({"revision": head})
        return
    output.line(f"Migrated the bank database to revision {head}.")


def _status(composition: Composition, output: Output) -> None:
    status = composition.schema().status()
    if output.as_json:
        output.json(
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


def _seed_reference(
    arguments: argparse.Namespace, settings: Settings, composition: Composition, output: Output
) -> None:
    bank_code = settings.require_bank_code()
    result = composition.seed_reference().run(arguments.data_dir, bank_code)
    if output.as_json:
        output.json({"bank_code": bank_code, "lenders": result.lenders, "facilities": result.facilities})
        return
    output.table(
        f"Reference data of {bank_code}",
        ("table", "rows"),
        (("lenders", f"{result.lenders:,}"), ("facilities", f"{result.facilities:,}")),
        "tables seeded",
        "Lenders and facilities are up to date.",
    )


def _run_row(run: StageRun) -> tuple[str, str, str, str, str]:
    return (
        str(run.submission_id),
        run.started_at_status.value,
        run.outcome.value,
        run.status.value,
        run.error_code or "",
    )


def _report(report: WorkerReport, output: Output) -> None:
    if output.as_json:
        keys = ("submission_id", "from", "outcome", "status", "error_code")
        output.json({"stages": [dict(zip(keys, _run_row(run), strict=True)) for run in report.runs]})
        return
    output.table(
        "Pipeline worker",
        ("submission", "from", "outcome", "status", "error"),
        [_run_row(run) for run in report.runs],
        "stages run",
        "Nothing else is runnable now.",
    )


def _worker(
    arguments: argparse.Namespace, settings: Settings, composition: Composition, output: Output
) -> None:
    configure_logging(settings.require_bank_code(), settings.log_level)
    composition.schema().require_head()
    worker = composition.worker()
    if arguments.once:
        _report(worker.run_once(), output)
        return
    stop = EventStopSignal()

    def request_stop(number: int, frame: FrameType | None) -> None:
        stop.request_stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    worker.run_until_stopped(stop)
