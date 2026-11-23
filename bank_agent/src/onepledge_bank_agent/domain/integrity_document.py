"""The integrity report as a document: the `IntegrityReport` wire shape, and the findings a stored one holds.

One walk of the report gives both renderings their facts (SPEC_DECISIONS 88): the JSON
document here, and the text report of the command line, which lists the same sections
and the same failures (`all_findings`).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import CheckpointFailure
from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.integrity import (
    AuditChainSection,
    CertificateFinding,
    CertificateProblem,
    CheckpointProblem,
    EvidenceProblem,
    EvidenceProblemKind,
    IntegrityReport,
    RawDataSection,
    RegistryChainSection,
    SectionStatus,
    parse_time,
    render_time,
)


def _id_list(values: Sequence[uuid.UUID]) -> list[JsonValue]:
    return [str(value) for value in values]


def _chain_json(chain: RegistryChainSection) -> dict[str, JsonValue]:
    last = chain.last_verified
    return {
        "status": chain.status.value,
        "checkpoints_received": chain.checkpoints_received,
        "checkpoints_verified": chain.checkpoints_verified,
        "last_verified": None
        if last is None
        else {
            "checkpoint_seq": last.checkpoint_seq,
            "last_event_seq": last.last_event_seq,
            "head_hash": last.head_hash,
            "verified_at": render_time(last.verified_at),
        },
        "failures": [
            {
                "checkpoint_id": str(p.checkpoint_id),
                "checkpoint_seq": p.checkpoint_seq,
                "failure_reason": p.failure_reason.value,
                "first_bad_event_seq": p.first_bad_event_seq,
                "received_at": render_time(p.received_at),
            }
            for p in chain.failures
        ],
    }


def report_json(report: IntegrityReport) -> dict[str, JsonValue]:
    """The `IntegrityReport` wire shape."""
    certificates, audit, raw = report.certificates, report.audit_log, report.raw_data
    return {
        "status": report.status.value,
        "generated_at": render_time(report.generated_at),
        "last_deep_verification_at": (
            render_time(report.last_deep_verification_at) if report.last_deep_verification_at else None
        ),
        "registry_chain": _chain_json(report.registry_chain),
        "certificates": {
            "status": certificates.status.value,
            "total": certificates.total,
            "verified": certificates.verified,
            "failures": [
                {"certificate_id": str(p.certificate_id), "failure_reason": p.failure_reason.value}
                for p in certificates.failures
            ],
        },
        "audit_log": {
            "status": audit.status.value,
            "entries": audit.entries,
            "last_seq": audit.last_seq,
            "first_bad_seq": audit.first_bad_seq,
        },
        "raw_data": {
            "status": raw.status.value,
            "submissions_checked": raw.submissions_checked,
            "rows_checked": raw.rows_checked,
            "bad_submission_ids": _id_list(raw.bad_submission_ids),
        },
        "database_controls": {
            "status": report.database_controls.status.value,
            "controls": [{"name": name.value, "ok": ok} for name, ok in report.database_controls.controls],
        },
        "registry_messages": {
            "status": report.registry_messages.status.value,
            "quarantined": report.registry_messages.quarantined,
        },
        "evidence": {
            "status": report.evidence.status.value,
            "problems": [
                {"table": p.table, "row_id": p.row_id, "problem": p.problem.value}
                for p in report.evidence.problems
            ],
        },
    }


def _object(value: JsonValue | None) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise InvalidValueError("report", "INVALID_TYPE")
    return value


def _list(value: JsonValue | None) -> Sequence[JsonValue]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise InvalidValueError("report", "INVALID_TYPE")
    return value


def _text(value: JsonValue | None) -> str:
    if not isinstance(value, str):
        raise InvalidValueError("report", "INVALID_TYPE")
    return value


def _optional_int(value: JsonValue | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidValueError("report", "INVALID_TYPE")
    return value


def _int(value: JsonValue | None) -> int:
    number = _optional_int(value)
    if number is None:
        raise InvalidValueError("report", "REQUIRED")
    return number


def _checkpoint_problem(entry: Mapping[str, JsonValue]) -> CheckpointProblem:
    return CheckpointProblem(
        uuid.UUID(_text(entry.get("checkpoint_id"))),
        _int(entry.get("checkpoint_seq")),
        CheckpointFailure(_text(entry.get("failure_reason"))),
        _optional_int(entry.get("first_bad_event_seq")),
        parse_time(_text(entry.get("received_at"))),
    )


@dataclass(frozen=True)
class StoredFindings:
    """What a stored deep verification report found."""

    checkpoints: tuple[CheckpointProblem, ...]
    certificates: tuple[CertificateProblem, ...]
    audit: AuditChainSection
    raw: RawDataSection
    evidence: tuple[EvidenceProblem, ...]
    tail: AuditTail | None


@dataclass(frozen=True)
class AuditTail:
    """The end of the audit log when a deep verification ran (SPEC_DECISIONS 89 c)."""

    last_seq: int
    last_entry_hash: str


def stored_findings(document: Mapping[str, JsonValue]) -> StoredFindings:
    """What a stored deep verification report found: checkpoint and certificate problems, audit, raw
    rows, evidence problems and the audit tail it saw."""
    chain, certificates = _object(document.get("registry_chain")), _object(document.get("certificates"))
    audit, raw = _object(document.get("audit_log")), _object(document.get("raw_data"))
    checkpoints = tuple(_checkpoint_problem(_object(entry)) for entry in _list(chain.get("failures")))
    certificate_problems = tuple(
        CertificateProblem(
            uuid.UUID(_text(_object(entry).get("certificate_id"))),
            CertificateFinding(_text(_object(entry).get("failure_reason"))),
        )
        for entry in _list(certificates.get("failures"))
    )
    audit_section = AuditChainSection(
        SectionStatus(_text(audit.get("status"))),
        _int(audit.get("entries")),
        _int(audit.get("last_seq")),
        _optional_int(audit.get("first_bad_seq")),
    )
    raw_section = RawDataSection(
        SectionStatus(_text(raw.get("status"))),
        _int(raw.get("submissions_checked")),
        _int(raw.get("rows_checked")),
        tuple(uuid.UUID(_text(value)) for value in _list(raw.get("bad_submission_ids"))),
    )
    evidence = document.get("evidence")
    problems = (
        tuple(_evidence_problem(_object(entry)) for entry in _list(_object(evidence).get("problems")))
        if evidence is not None
        else ()
    )
    tail_document = document.get(TAIL_KEY)
    tail = None
    if tail_document is not None:
        found = _object(tail_document)
        tail = AuditTail(_int(found.get("last_seq")), _text(found.get("last_entry_hash")))
    return StoredFindings(checkpoints, certificate_problems, audit_section, raw_section, problems, tail)


TAIL_KEY = "audit_tail"
"""The key of the stored report (not the API document) that keeps the audit tail of decision 89 (c)."""


def stored_document(report: IntegrityReport, tail: AuditTail | None) -> dict[str, JsonValue]:
    """What `integrity_runs.report` keeps: the report document and the audit tail it saw."""
    document = report_json(report)
    if tail is not None:
        document[TAIL_KEY] = {"last_seq": tail.last_seq, "last_entry_hash": tail.last_entry_hash}
    return document


def _evidence_problem(entry: Mapping[str, JsonValue]) -> EvidenceProblem:
    row_id = entry.get("row_id")
    return EvidenceProblem(
        _text(entry.get("table")),
        row_id if isinstance(row_id, str) else None,
        EvidenceProblemKind(_text(entry.get("problem"))),
    )


@dataclass(frozen=True)
class Finding:
    """One line of "Open failures" or "Recovered (evidence kept)": section, object, id, reason, where."""

    section: str
    subject: str
    ident: str
    reason: str
    where: str = ""


def _checkpoint_findings(chain: RegistryChainSection) -> list[Finding]:
    return [
        Finding(
            "registry_chain", "checkpoint", str(p.checkpoint_id), p.failure_reason.value,
            f"seq {p.checkpoint_seq}"
            + (f", first bad event {p.first_bad_event_seq}" if p.first_bad_event_seq is not None else ""),
        )
        for p in chain.failures
    ]  # fmt: skip


def all_findings(report: IntegrityReport) -> tuple[list[Finding], list[Finding]]:
    """Every finding that makes a section BROKEN now, and the failures since recovered (evidence kept)."""
    open_findings = _checkpoint_findings(report.registry_chain)
    recovered: list[Finding] = []
    for problem in report.certificates.failures:
        finding = Finding(
            "certificates", "certificate", str(problem.certificate_id), problem.failure_reason.value
        )
        (recovered if problem.recovered else open_findings).append(finding)
    if report.audit_log.first_bad_seq is not None:
        open_findings.append(
            Finding("audit_log", "audit entry", str(report.audit_log.first_bad_seq), "CHAIN_BREAK")
        )
    open_findings += [
        Finding("raw_data", "submission", str(sid), "RAW_DATA_MISMATCH")
        for sid in report.raw_data.bad_submission_ids
    ]
    open_findings += [
        Finding("database_controls", "control", name.value, "NOT_IN_FORCE")
        for name, ok in report.database_controls.controls
        if not ok
    ]
    for violation in report.database_controls.violations:
        code, _, name = violation.partition(" on ")
        subject = "schema" if code.startswith("SCHEMA_OBJECT_") else "role"
        open_findings.append(Finding("database_controls", subject, name, code))
    for m in report.registry_messages.messages:
        finding = Finding("registry_messages", "message", str(m.quarantine_id), m.reason, m.message_type)
        (open_findings if m.open else recovered).append(finding)
    outbox = report.registry_messages.outbox
    if outbox is not None and outbox.blind:
        since = render_time(outbox.blind_since) if outbox.blind_since is not None else "the start"
        where = f"since {since}" + (f", {outbox.reason}" if outbox.reason else "")
        open_findings.append(Finding("registry_messages", "outbox", "", "OUTBOX_BLIND", where))
    open_findings += [
        Finding("evidence", p.table, p.row_id or "", p.problem.value) for p in report.evidence.problems
    ]
    return open_findings, recovered
