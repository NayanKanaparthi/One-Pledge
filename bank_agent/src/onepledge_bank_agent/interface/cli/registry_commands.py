"""`onepledge-bank registry sync`, `integrity report` and `integrity verify` (SPEC 19, 14.1, 15.5, 15.6).

They run as the app role with the runtime settings. A sync and a deep verification
are audited with the system actor and `{"via": "cli"}`. The report prints as aligned
sections; `--json` prints the `IntegrityReport` document with `"ok": true`.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from types import MappingProxyType

from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.sync_registry_outbox import SyncResult
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import IntegrityRunStatus
from onepledge_bank_agent.domain.integrity import IntegrityReport, render_time
from onepledge_bank_agent.domain.integrity_document import Finding, all_findings, report_json
from onepledge_bank_agent.interface.cli.output import Output, counted
from onepledge_bank_agent.interface.composition import RuntimeComposition


def registry_sync(arguments: argparse.Namespace, composition: RuntimeComposition, output: Output) -> int:
    """Pull and process every pending registry message now."""
    result = composition.outbox_sync().requested(Requester.command_line())
    if result.failure is not None:  # the registry could not be read: the failure's own sentence and code
        raise result.failure
    _sync_result(result, output)
    if result.complete:
        return 0
    output.error(_incomplete(result))
    return 1


def integrity_report(arguments: argparse.Namespace, composition: RuntimeComposition, output: Output) -> int:
    """Print the live integrity report; exit 1 when it is BROKEN."""
    return print_report(composition.integrity_report().execute(), output, "Integrity report")


def integrity_verify(arguments: argparse.Namespace, composition: RuntimeComposition, output: Output) -> int:
    """Run a deep verification, store it and print its report; exit 1 when it is BROKEN."""
    return print_report(
        composition.deep_verification().execute(Requester.command_line()), output, "Deep verification"
    )


def sync_incomplete_sentence(reason: str) -> str:
    """Why an outbox sync did not take in everything, and what to do (SPEC_DECISIONS 101)."""
    return (
        f"The registry outbox sync was incomplete (reason {reason}). Run registry sync again; "
        "integrity report shows what is waiting."
    )


def _incomplete(result: SyncResult) -> str:
    if result.skipped:
        return "Another registry sync kept the outbox busy for two minutes; nothing was done. Run it again."
    return sync_incomplete_sentence(result.reason or "UNKNOWN")


def _sync_result(result: SyncResult, output: Output) -> None:
    if output.as_json:
        facts: dict[str, JsonValue] = {
            **result.counts(),
            "skipped": result.skipped,
            "complete": result.complete,
        }
        if result.complete:
            output.success(facts)
        else:
            code = "OUTBOX_SYNC_INCOMPLETE"
            output.json({"ok": False, **facts, "error": {"code": code, "message": _incomplete(result)}})
        return
    if result.skipped:
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


def _chain_facts(report: IntegrityReport) -> str:
    chain, last = report.registry_chain, report.registry_chain.last_verified
    last_text = f"; last verified seq {last.checkpoint_seq:,}" if last is not None else ""
    received = counted(chain.checkpoints_received, "checkpoint", "checkpoints")
    return f"{received} received, {chain.checkpoints_verified:,} verified{last_text}"


def _audit_facts(report: IntegrityReport) -> str:
    audit = report.audit_log
    bad = f"; first bad seq {audit.first_bad_seq:,}" if audit.first_bad_seq is not None else ""
    return f"{counted(audit.entries, 'entry', 'entries')}{bad}"


def _controls_facts(report: IntegrityReport) -> str:
    controls = report.database_controls.controls
    return f"{sum(1 for _, ok in controls if ok)} of {len(controls)} in force"


FACTS: Mapping[str, Callable[[IntegrityReport], str]] = MappingProxyType(
    {
        "registry_chain": _chain_facts,
        "certificates": lambda r: (
            f"{r.certificates.verified:,} of "
            + counted(r.certificates.total, "answered request", "answered requests")
            + " proven"
        ),
        "audit_log": _audit_facts,
        "raw_data": lambda r: (
            counted(r.raw_data.submissions_checked, "submission", "submissions")
            + f", {len(r.raw_data.bad_submission_ids):,} not intact"
        ),
        "database_controls": _controls_facts,
        "registry_messages": lambda r: (
            counted(r.registry_messages.quarantined, "message", "messages") + " quarantined"
        ),
        "evidence": lambda r: counted(len(r.evidence.problems), "problem", "problems"),
    }
)
"""The facts column of every section of the document, by section name."""


def _unread(sqlstate: str) -> str:
    """The facts of a section the report could not read (SPEC_DECISIONS 93, item 8)."""
    return f"not read: the database refused the query (SQLSTATE {sqlstate}); run db migrate"


def _finding_text(finding: Finding) -> str:
    """`ID: REASON, where`: the last column of a failure row, which wraps under itself."""
    text = f"{finding.ident}: {finding.reason}" if finding.ident else finding.reason
    return f"{text}, {finding.where}" if finding.where else text


def _rows(findings: list[Finding]) -> list[tuple[str, str, str]]:
    return [(f.section, f.subject, _finding_text(f)) for f in findings]


def print_report(report: IntegrityReport, output: Output, title: str) -> int:
    """Print the whole report; exit 1 when it is BROKEN (SPEC_DECISIONS 78)."""
    document = report_json(report)
    code = 0 if report.status is IntegrityRunStatus.OK else 1
    if output.as_json:
        output.success(document)
        return code
    deep = report.last_deep_verification_at
    output.pairs(
        title,
        {
            "status": report.status.value,
            "generated at": render_time(report.generated_at),
            "last deep verification": render_time(deep) if deep is not None else "never",
        },
    )
    sections = [name for name, value in document.items() if isinstance(value, dict) and "status" in value]
    unread = {u.section: u.sqlstate for u in report.unread}
    rows = [
        (
            name,
            str(_section_status(document, name)),
            _unread(unread[name]) if name in unread else FACTS[name](report),
        )
        for name in sections
    ]
    output.table("Sections", ("section", "status", "facts"), rows, ("section", "sections"), "")
    open_findings, recovered = all_findings(report)
    headers = ("section", "object", "finding")
    if open_findings:
        output.table("Open failures", headers, _rows(open_findings), ("failure", "failures"), "")
    if recovered:
        output.table("Recovered (evidence kept)", headers, _rows(recovered), ("failure", "failures"), "")
    output.line(f"Overall status {report.status.value}: BROKEN when any section is BROKEN.")
    return code


def _section_status(document: Mapping[str, JsonValue], name: str) -> JsonValue:
    section = document[name]
    return section.get("status") if isinstance(section, Mapping) else None
