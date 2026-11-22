"""`onepledge-bank registry sync`, `integrity report` and `integrity verify` (SPEC 19, 14.1, 15.5, 15.6).

They run as the app role with the runtime settings. A sync and a deep verification
are audited with the system actor and `{"via": "cli"}`. The report prints as aligned
sections; `--json` prints the `IntegrityReport` document with `"ok": true`.
"""

from __future__ import annotations

import argparse

from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.sync_registry_outbox import SyncResult
from onepledge_bank_agent.domain.integrity import IntegrityReport, render_time, report_json
from onepledge_bank_agent.interface.cli.output import Output, counted, short_hash
from onepledge_bank_agent.interface.composition import RuntimeComposition


def registry_sync(arguments: argparse.Namespace, composition: RuntimeComposition, output: Output) -> None:
    """Pull and process every pending registry message now."""
    result = composition.outbox_sync().requested(Requester.command_line())
    _sync_result(result, output)


def integrity_report(arguments: argparse.Namespace, composition: RuntimeComposition, output: Output) -> None:
    """Print the live integrity report."""
    _report(composition.integrity_report().execute(), output, "Integrity report")


def integrity_verify(arguments: argparse.Namespace, composition: RuntimeComposition, output: Output) -> None:
    """Run a deep verification, store it and print its report."""
    _report(composition.deep_verification().execute(Requester.command_line()), output, "Deep verification")


def _sync_result(result: SyncResult, output: Output) -> None:
    if output.as_json:
        output.success({**result.counts(), "skipped": result.skipped})
        return
    if result.skipped:
        output.line("Another registry sync is running for this bank; nothing was done.")
        return
    output.pairs(
        "Registry sync",
        {
            "messages processed": f"{result.messages_processed:,}",
            "alerts created": f"{result.alerts_created:,}",
            "case updates applied": f"{result.case_updates_applied:,}",
            "checkpoints verified": f"{result.checkpoints_verified:,}",
            "checkpoints failed": f"{result.checkpoints_failed:,}",
        },
    )


def _sections(report: IntegrityReport) -> list[tuple[str, str, str]]:
    chain, certificates, audit, raw = (
        report.registry_chain,
        report.certificates,
        report.audit_log,
        report.raw_data,
    )
    last = chain.last_verified
    last_text = (
        f"; last verified seq {last.checkpoint_seq:,} at event {last.last_event_seq:,}, head "
        f"{short_hash(last.head_hash)}"
        if last is not None
        else ""
    )
    controls = report.database_controls.controls
    in_force = sum(1 for _, ok in controls if ok)
    first_bad = f"; first bad seq {audit.first_bad_seq:,}" if audit.first_bad_seq is not None else ""
    return [
        (
            "registry_chain",
            chain.status.value,
            f"{counted(chain.checkpoints_received, 'checkpoint', 'checkpoints')} received, "
            f"{chain.checkpoints_verified:,} verified{last_text}",
        ),
        (
            "certificates",
            certificates.status.value,
            counted(certificates.total, "certificate", "certificates")
            + f", {certificates.verified:,} verified",
        ),
        ("audit_log", audit.status.value, f"{counted(audit.entries, 'entry', 'entries')}{first_bad}"),
        (
            "raw_data",
            raw.status.value,
            f"{counted(raw.submissions_checked, 'submission', 'submissions')}, "
            f"{counted(raw.rows_checked, 'row', 'rows')} checked, {len(raw.bad_submission_ids):,} not intact",
        ),
        (
            "database_controls",
            report.database_controls.status.value,
            f"{in_force} of {len(controls)} in force",
        ),
    ]


def _failures(report: IntegrityReport) -> list[tuple[str, str, str, str]]:
    rows = [
        (
            "checkpoint",
            str(problem.checkpoint_id),
            problem.failure_reason.value,
            f"seq {problem.checkpoint_seq:,}"
            + (f", first bad event {problem.first_bad_event_seq:,}" if problem.first_bad_event_seq else ""),
        )
        for problem in report.registry_chain.failures
    ]
    rows += [
        ("certificate", str(problem.certificate_id), problem.failure_reason.value, "")
        for problem in report.certificates.failures
    ]
    rows += [("submission", str(sid), "RAW_DATA_MISMATCH", "") for sid in report.raw_data.bad_submission_ids]
    return rows


def _report(report: IntegrityReport, output: Output, title: str) -> None:
    if output.as_json:
        output.success(report_json(report))
        return
    deep = report.last_deep_verification_at
    output.pairs(
        title,
        {
            "status": report.status.value,
            "generated at": render_time(report.generated_at),
            "last deep verification": render_time(deep) if deep is not None else "never",
        },
    )
    output.table("Sections", ("section", "status", "facts"), _sections(report), ("section", "sections"), "")
    failures = _failures(report)
    if failures:
        output.table("Failures", ("object", "id", "reason", "where"), failures, ("failure", "failures"), "")
    output.table(
        "Database controls",
        ("control", "in force"),
        [(name.value, "yes" if ok else "no") for name, ok in report.database_controls.controls],
        ("control", "controls"),
        f"Overall status {report.status.value}: BROKEN when any section is BROKEN.",
    )
