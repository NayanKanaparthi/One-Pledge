"""The integrity report (SPEC 15.5, 15.6; `IntegrityReport` in `contracts/bank_agent.yaml`).

Five sections: what the bank verified about the registry (checkpoints and the chain),
the certificates it holds, its own audit chain, its raw rows, and the database controls
in force. `GET /v1/integrity` combines live counts with the last deep verification; a
deep verification stores the report it produced. A section is NOT_VERIFIED when nothing
has been verified yet, BROKEN when anything failed, OK otherwise; the report is BROKEN
when any section is.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import CertificateFailure, CheckpointFailure, IntegrityRunStatus
from onepledge_bank_agent.domain.errors import InvalidValueError


class SectionStatus(StrEnum):
    """Status of one section."""

    OK = "OK"
    BROKEN = "BROKEN"
    NOT_VERIFIED = "NOT_VERIFIED"


class DatabaseControl(StrEnum):
    """The eleven database controls of SPEC 15.5, in report order."""

    LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED = "LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED"
    AUDIT_LOG_IMMUTABILITY_TRIGGER_ENABLED = "AUDIT_LOG_IMMUTABILITY_TRIGGER_ENABLED"
    EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED = "EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED"
    LOAN_STATE_GUARD_TRIGGER_ENABLED = "LOAN_STATE_GUARD_TRIGGER_ENABLED"
    AUDIT_CHAIN_GUARD_TRIGGER_ENABLED = "AUDIT_CHAIN_GUARD_TRIGGER_ENABLED"
    APP_ROLE_CANNOT_UPDATE_LOANS_RAW = "APP_ROLE_CANNOT_UPDATE_LOANS_RAW"
    APP_ROLE_CANNOT_DELETE_LOANS_RAW = "APP_ROLE_CANNOT_DELETE_LOANS_RAW"
    APP_ROLE_CANNOT_UPDATE_AUDIT_LOG = "APP_ROLE_CANNOT_UPDATE_AUDIT_LOG"
    APP_ROLE_CANNOT_DELETE_AUDIT_LOG = "APP_ROLE_CANNOT_DELETE_AUDIT_LOG"
    APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER = "APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER"
    SCHEMA_AT_MIGRATION_HEAD = "SCHEMA_AT_MIGRATION_HEAD"


def render_time(moment: datetime) -> str:
    """RFC 3339 UTC with `Z`, microseconds kept when present."""
    text = moment.astimezone(UTC).isoformat()
    return text.replace("+00:00", "Z")


def parse_time(text: str) -> datetime:
    """The inverse of `render_time`."""
    return datetime.fromisoformat(text)


@dataclass(frozen=True)
class CheckpointRef:
    """The last verified checkpoint."""

    checkpoint_seq: int
    last_event_seq: int
    head_hash: str
    verified_at: datetime


@dataclass(frozen=True)
class CheckpointProblem:
    """A failed checkpoint row, or a deep verification finding about a stored checkpoint."""

    checkpoint_id: uuid.UUID
    checkpoint_seq: int
    failure_reason: CheckpointFailure
    first_bad_event_seq: int | None
    received_at: datetime


@dataclass(frozen=True)
class CertificateProblem:
    """A certificate that failed verification, at receipt or in a deep verification."""

    certificate_id: uuid.UUID
    failure_reason: CertificateFailure


@dataclass(frozen=True)
class RegistryChainSection:
    """Checkpoints received and verified, the last verified one, and every failure."""

    status: SectionStatus
    checkpoints_received: int
    checkpoints_verified: int
    last_verified: CheckpointRef | None
    failures: tuple[CheckpointProblem, ...]


@dataclass(frozen=True)
class CertificateSection:
    """Certificates held, how many verify, and the failures."""

    status: SectionStatus
    total: int
    verified: int
    failures: tuple[CertificateProblem, ...]


@dataclass(frozen=True)
class AuditChainSection:
    """The audit log's size and the result of the last deep verification."""

    status: SectionStatus
    entries: int
    last_seq: int
    first_bad_seq: int | None


@dataclass(frozen=True)
class RawDataSection:
    """The result of the last deep verification of the raw rows."""

    status: SectionStatus
    submissions_checked: int
    rows_checked: int
    bad_submission_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class ControlsSection:
    """Each database control and whether it is in force now."""

    status: SectionStatus
    controls: tuple[tuple[DatabaseControl, bool], ...]


@dataclass(frozen=True)
class IntegrityReport:
    """The five sections, when the report was made and when the last deep verification ran."""

    generated_at: datetime
    last_deep_verification_at: datetime | None
    registry_chain: RegistryChainSection
    certificates: CertificateSection
    audit_log: AuditChainSection
    raw_data: RawDataSection
    database_controls: ControlsSection

    @property
    def status(self) -> IntegrityRunStatus:
        """BROKEN when any section is BROKEN; NOT_VERIFIED sections do not break the report."""
        statuses = (
            self.registry_chain.status,
            self.certificates.status,
            self.audit_log.status,
            self.raw_data.status,
            self.database_controls.status,
        )
        broken = SectionStatus.BROKEN in statuses
        return IntegrityRunStatus.BROKEN if broken else IntegrityRunStatus.OK

    def section_statuses(self) -> dict[str, str]:
        """Status per section, for the audit entry of a deep verification."""
        return {
            "registry_chain": self.registry_chain.status.value,
            "certificates": self.certificates.status.value,
            "audit_log": self.audit_log.status.value,
            "raw_data": self.raw_data.status.value,
            "database_controls": self.database_controls.status.value,
        }


def broken_if(failed: bool) -> SectionStatus:
    """BROKEN or OK."""
    return SectionStatus.BROKEN if failed else SectionStatus.OK


def merge_checkpoint_problems(*groups: Iterable[CheckpointProblem]) -> tuple[CheckpointProblem, ...]:
    """Every distinct problem, ordered by checkpoint seq, then reason."""
    unique = {(p.checkpoint_id, p.failure_reason, p.first_bad_event_seq): p for g in groups for p in g}
    return tuple(sorted(unique.values(), key=_problem_order))


def _problem_order(problem: CheckpointProblem) -> tuple[int, str, str]:
    return problem.checkpoint_seq, problem.failure_reason.value, str(problem.checkpoint_id)


def merge_certificate_problems(*groups: Iterable[CertificateProblem]) -> tuple[CertificateProblem, ...]:
    """One problem per certificate (the first one given wins), ordered by certificate id."""
    unique: dict[uuid.UUID, CertificateProblem] = {}
    for group in groups:
        for problem in group:
            unique.setdefault(problem.certificate_id, problem)
    return tuple(sorted(unique.values(), key=lambda p: str(p.certificate_id)))


def chain_section(
    received: int, verified: int, last: CheckpointRef | None, failures: tuple[CheckpointProblem, ...]
) -> RegistryChainSection:
    """NOT_VERIFIED before the first checkpoint, BROKEN with any failure, else OK."""
    if failures:
        status = SectionStatus.BROKEN
    else:
        status = SectionStatus.NOT_VERIFIED if received == 0 else SectionStatus.OK
    return RegistryChainSection(status, received, verified, last, failures)


def certificate_section(total: int, failures: tuple[CertificateProblem, ...]) -> CertificateSection:
    """BROKEN with any failure; `verified` counts the certificates with none."""
    return CertificateSection(broken_if(bool(failures)), total, total - len(failures), failures)


def controls_section(controls: Mapping[DatabaseControl, bool]) -> ControlsSection:
    """Every control in report order; BROKEN when any is false (a missing one counts as false)."""
    ordered = tuple((control, bool(controls.get(control, False))) for control in DatabaseControl)
    return ControlsSection(broken_if(not all(ok for _, ok in ordered)), ordered)


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


def stored_findings(
    document: Mapping[str, JsonValue],
) -> tuple[tuple[CheckpointProblem, ...], tuple[CertificateProblem, ...], AuditChainSection, RawDataSection]:
    """What a stored deep verification report found: checkpoint and certificate problems, audit, raw rows."""
    chain, certificates = _object(document.get("registry_chain")), _object(document.get("certificates"))
    audit, raw = _object(document.get("audit_log")), _object(document.get("raw_data"))
    checkpoints = tuple(_checkpoint_problem(_object(entry)) for entry in _list(chain.get("failures")))
    certificate_problems = tuple(
        CertificateProblem(
            uuid.UUID(_text(_object(entry).get("certificate_id"))),
            CertificateFailure(_text(_object(entry).get("failure_reason"))),
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
    return checkpoints, certificate_problems, audit_section, raw_section
