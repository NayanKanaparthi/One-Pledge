"""The integrity report (SPEC 15.5, 15.6; `IntegrityReport` in `contracts/bank_agent.yaml`).

Seven sections: what the bank verified about the registry (checkpoints and the chain),
the certificates it holds, its own audit chain, its raw rows, the database controls in
force, the registry messages it had to quarantine (SPEC_DECISIONS 57, 85), and the
evidence whose rows and audit entries no longer match (SPEC_DECISIONS 89).
`GET /v1/integrity` combines live counts with the last deep verification; a deep
verification stores the report it produced. A section is NOT_VERIFIED when nothing
has been verified yet, BROKEN when anything failed, OK otherwise; the report is BROKEN
when any section is.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from onepledge_bank_agent.domain.enums import (
    CertificateFailure,
    CheckpointFailure,
    IntegrityRunStatus,
    TapeStatus,
)


class SectionStatus(StrEnum):
    """Status of one section."""

    OK = "OK"
    BROKEN = "BROKEN"
    NOT_VERIFIED = "NOT_VERIFIED"


class DatabaseControl(StrEnum):
    """The fifteen database controls of SPEC 15.5 and SPEC_DECISIONS 47, 72, 103 and 105, in report order."""

    LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED = "LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED"
    AUDIT_LOG_IMMUTABILITY_TRIGGER_ENABLED = "AUDIT_LOG_IMMUTABILITY_TRIGGER_ENABLED"
    EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED = "EVIDENCE_TABLES_IMMUTABILITY_TRIGGERS_ENABLED"
    LOAN_STATE_GUARD_TRIGGER_ENABLED = "LOAN_STATE_GUARD_TRIGGER_ENABLED"
    ALERT_STATE_GUARD_TRIGGER_ENABLED = "ALERT_STATE_GUARD_TRIGGER_ENABLED"
    AUDIT_CHAIN_GUARD_TRIGGER_ENABLED = "AUDIT_CHAIN_GUARD_TRIGGER_ENABLED"
    APP_ROLE_CANNOT_UPDATE_LOANS_RAW = "APP_ROLE_CANNOT_UPDATE_LOANS_RAW"
    APP_ROLE_CANNOT_DELETE_LOANS_RAW = "APP_ROLE_CANNOT_DELETE_LOANS_RAW"
    APP_ROLE_CANNOT_UPDATE_AUDIT_LOG = "APP_ROLE_CANNOT_UPDATE_AUDIT_LOG"
    APP_ROLE_CANNOT_DELETE_AUDIT_LOG = "APP_ROLE_CANNOT_DELETE_AUDIT_LOG"
    APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER = "APP_ROLE_IS_NOT_OWNER_OR_SUPERUSER"
    APP_ROLE_GRANTS_EXACT = "APP_ROLE_GRANTS_EXACT"
    TRIGGERS_FIRE_FOR_APP_ROLE = "TRIGGERS_FIRE_FOR_APP_ROLE"
    SCHEMA_AT_MIGRATION_HEAD = "SCHEMA_AT_MIGRATION_HEAD"
    SCHEMA_MATCHES_MANIFEST = "SCHEMA_MATCHES_MANIFEST"


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
    """A certificate that failed verification (at receipt or in a deep verification), or one the
    registry announced that the bank does not hold verified (CERTIFICATE_MISSING, decision 89).
    `recovered` is true for a failed row whose request has since got a verified row."""

    certificate_id: uuid.UUID
    failure_reason: CertificateFinding
    recovered: bool = field(default=False, compare=False)


class CertificateFinding(StrEnum):
    """What the certificates section reports: a verification failure or a missing proof."""

    BAD_SIGNATURE = CertificateFailure.BAD_SIGNATURE.value
    UNKNOWN_SIGNING_KEY = CertificateFailure.UNKNOWN_SIGNING_KEY.value
    BODY_HASH_MISMATCH = CertificateFailure.BODY_HASH_MISMATCH.value
    CONTENT_MISMATCH = CertificateFailure.CONTENT_MISMATCH.value
    CERTIFICATE_MISSING = "CERTIFICATE_MISSING"


class EvidenceProblemKind(StrEnum):
    """What deep verification and the readers found (SPEC_DECISIONS 89 b, 100, 107)."""

    ROW_MISSING = "ROW_MISSING"
    ROW_UNAUDITED = "ROW_UNAUDITED"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    DIGEST_MISMATCH = "DIGEST_MISMATCH"
    DERIVATION_MISMATCH = "DERIVATION_MISMATCH"
    STATE_MISMATCH = "STATE_MISMATCH"
    UNREADABLE = "UNREADABLE"


@dataclass(frozen=True, order=True)
class EvidenceProblem:
    """One evidence finding: the table, the row (a primary key, or the missing seq), the problem, and
    for a stage digest the stage whose entry holds it (SPEC_DECISIONS 107, 123)."""

    table: str
    row_id: str | None
    problem: EvidenceProblemKind
    stage: TapeStatus | None = field(default=None, compare=False)


@dataclass(frozen=True)
class EvidenceSection:
    """The `evidence` section: BROKEN while any problem is listed."""

    status: SectionStatus
    problems: tuple[EvidenceProblem, ...]


@dataclass(frozen=True)
class QuarantineFinding:
    """A quarantined registry message: its row, its id (None when it had no usable one), its type, why,
    when, whether its payload was withheld, and whether it is open (SPEC_DECISIONS 101, 108). A row
    is open unless the message was applied late."""

    quarantine_id: uuid.UUID
    message_id: uuid.UUID | None
    message_type: str
    reason: str
    received_at: datetime
    payload_withheld: bool
    open: bool


@dataclass(frozen=True)
class OutboxView:
    """The outbox as the report shows it: the last complete sync, whether the bank is blind, since
    when and why (SPEC_DECISIONS 101)."""

    last_complete_sync_at: datetime | None
    blind: bool
    blind_since: datetime | None
    reason: str | None


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
    """Requests that should have a certificate, how many have a verified one, and every failed row.

    BROKEN only while some request has a failed row and no verified one, or a deep
    verification found a verified row whose proof no longer holds (SPEC_DECISIONS 61).
    """

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
class ControlViolation:
    """Why a control is false: the control, the violation code and the object it names (SPEC_DECISIONS
    72, 103, 105, 108)."""

    control: DatabaseControl
    code: str
    object: str | None


@dataclass(frozen=True)
class ControlsSection:
    """Each database control and whether it is in force now, and every violation behind a false one."""

    status: SectionStatus
    controls: tuple[tuple[DatabaseControl, bool], ...]
    violations: tuple[ControlViolation, ...] = ()


@dataclass(frozen=True)
class RegistryMessagesSection:
    """Registry outbox messages quarantined (SPEC_DECISIONS 57, 101), each with its reason, and the
    outbox state; None when the section could not be read."""

    status: SectionStatus
    quarantined: int
    messages: tuple[QuarantineFinding, ...] = ()
    outbox: OutboxView | None = None


@dataclass(frozen=True)
class UnreadSection:
    """A section the live report could not read: the store's SQLSTATE (SPEC_DECISIONS 93, item 8).
    The section is shown NOT_VERIFIED and the rest of the report is still printed."""

    section: str
    sqlstate: str


@dataclass(frozen=True)
class IntegrityReport:
    """The seven sections, when the report was made and when the last deep verification ran."""

    generated_at: datetime
    last_deep_verification_at: datetime | None
    registry_chain: RegistryChainSection
    certificates: CertificateSection
    audit_log: AuditChainSection
    raw_data: RawDataSection
    database_controls: ControlsSection
    registry_messages: RegistryMessagesSection
    evidence: EvidenceSection
    unread: tuple[UnreadSection, ...] = ()

    @property
    def status(self) -> IntegrityRunStatus:
        """BROKEN when any section is BROKEN; NOT_VERIFIED sections do not break the report."""
        statuses = (
            self.registry_chain.status,
            self.certificates.status,
            self.audit_log.status,
            self.raw_data.status,
            self.database_controls.status,
            self.registry_messages.status,
            self.evidence.status,
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
            "registry_messages": self.registry_messages.status.value,
            "evidence": self.evidence.status.value,
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
    """Every distinct (certificate, reason) pair, ordered by certificate id, then reason."""
    unique = {(p.certificate_id, p.failure_reason): p for group in groups for p in group}
    return tuple(sorted(unique.values(), key=lambda p: (str(p.certificate_id), p.failure_reason.value)))


def chain_section(
    counts: tuple[int, int],
    last: CheckpointRef | None,
    failures: tuple[CheckpointProblem, ...],
    answered: bool,
) -> RegistryChainSection:
    """BROKEN with any failure, or when the registry has answered and no checkpoint is verified
    (SPEC_DECISIONS 89 a); NOT_VERIFIED only for a bank that never had an answer; else OK."""
    received, verified = counts
    if failures or (answered and verified == 0):
        status = SectionStatus.BROKEN
    else:
        status = SectionStatus.NOT_VERIFIED if received == 0 else SectionStatus.OK
    return RegistryChainSection(status, received, verified, last, failures)


def certificate_section(
    total: int, verified: int, failures: tuple[CertificateProblem, ...], broken: bool
) -> CertificateSection:
    """The section; `broken` is whether a request is unproven or a deep finding exists."""
    return CertificateSection(broken_if(broken), total, verified, failures)


def registry_messages_section(
    messages: Sequence[QuarantineFinding], outbox: OutboxView
) -> RegistryMessagesSection:
    """BROKEN while a quarantined message is open or the bank is blind (SPEC_DECISIONS 101)."""
    broken = outbox.blind or any(m.open for m in messages)
    return RegistryMessagesSection(broken_if(broken), len(messages), tuple(messages), outbox)


def evidence_section(problems: Iterable[EvidenceProblem] | None) -> EvidenceSection:
    """NOT_VERIFIED before the first deep verification; BROKEN while any problem is listed."""
    if problems is None:
        return EvidenceSection(SectionStatus.NOT_VERIFIED, ())
    found = tuple(sorted(set(problems)))
    return EvidenceSection(broken_if(bool(found)), found)


def controls_section(
    controls: Mapping[DatabaseControl, bool], violations: Sequence[ControlViolation] = ()
) -> ControlsSection:
    """Every control in report order; BROKEN when any is false (a missing one counts as false)."""
    ordered = tuple((control, bool(controls.get(control, False))) for control in DatabaseControl)
    return ControlsSection(broken_if(not all(ok for _, ok in ordered)), ordered, tuple(violations))
