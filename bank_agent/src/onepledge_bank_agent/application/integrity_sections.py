"""Building the sections of the integrity report from stored rows (SPEC 15.5), shared by both use cases."""

from __future__ import annotations

import uuid
from collections.abc import Sequence, Set
from datetime import datetime, timedelta

from onepledge_bank_agent.application.section_reads import SectionReads
from onepledge_bank_agent.domain.checkpoint_rules import DeepFinding
from onepledge_bank_agent.domain.entities import Certificate, Checkpoint
from onepledge_bank_agent.domain.integrity import (
    CertificateFinding,
    CertificateProblem,
    CertificateSection,
    CheckpointProblem,
    CheckpointRef,
    OutboxView,
    QuarantineFinding,
    RegistryChainSection,
    RegistryMessagesSection,
    certificate_section,
    chain_section,
    merge_certificate_problems,
    merge_checkpoint_problems,
    registry_messages_section,
)
from onepledge_bank_agent.domain.registry_records import QuarantinedMessage


def failed_checkpoints(checkpoints: Sequence[Checkpoint]) -> list[CheckpointProblem]:
    """Every stored checkpoint that failed verification when it was received."""
    return [
        CheckpointProblem(
            row.checkpoint_id, row.checkpoint_seq, row.failure, row.first_bad_event_seq, row.received_at
        )
        for row in checkpoints
        if row.failure is not None
    ]


def deep_checkpoint_problems(findings: Sequence[DeepFinding]) -> list[CheckpointProblem]:
    """Deep verification findings, each attributed to the stored checkpoint it concerns."""
    return [
        CheckpointProblem(
            finding.checkpoint.checkpoint_id,
            finding.checkpoint.checkpoint_seq,
            finding.failure,
            finding.first_bad_event_seq,
            finding.checkpoint.received_at,
        )
        for finding in findings
    ]


def last_verified(checkpoints: Sequence[Checkpoint]) -> CheckpointRef | None:
    """The verified checkpoint with the highest seq."""
    verified = [row for row in checkpoints if row.verified]
    if not verified:
        return None
    row = max(verified, key=lambda checkpoint: checkpoint.checkpoint_seq)
    return CheckpointRef(row.checkpoint_seq, row.last_event_seq, row.head_hash.value, row.verified_at)


def registry_chain(
    checkpoints: Sequence[Checkpoint], extra: Sequence[CheckpointProblem], answered: bool
) -> RegistryChainSection:
    """Counts, the last verified checkpoint, and the stored failures together with `extra` ones;
    `answered` is whether any registry request has a stored answer (SPEC_DECISIONS 89 a)."""
    failures = merge_checkpoint_problems(failed_checkpoints(checkpoints), extra)
    verified = sum(1 for row in checkpoints if row.verified)
    return chain_section((len(checkpoints), verified), last_verified(checkpoints), failures, answered)


def failed_certificates(certificates: Sequence[Certificate]) -> list[CertificateProblem]:
    """Every stored row that failed verification when it was received (kept as evidence), marked
    recovered when its request has since got a verified row."""
    proven = {row.request_id for row in certificates if row.verified}
    return [
        CertificateProblem(
            row.certificate_id, CertificateFinding(row.failure.value), row.request_id in proven
        )
        for row in certificates
        if row.failure is not None
    ]


def unproven_requests(certificates: Sequence[Certificate]) -> set[uuid.UUID]:
    """Requests with a failed row and no verified one: the bank holds no valid proof of them."""
    proven = {row.request_id for row in certificates if row.verified}
    return {row.request_id for row in certificates if not row.verified} - proven


def certificates_section(
    expected: int,
    certificates: Sequence[Certificate],
    deep: Sequence[CertificateProblem],
    missing: Sequence[tuple[uuid.UUID, uuid.UUID]],
) -> CertificateSection:
    """SPEC_DECISIONS 61 and 89: `total` is the answered requests, `verified` those with a verified
    row, `failures` every failed row ever stored (recovered ones marked), the deep findings, and a
    CERTIFICATE_MISSING for every answered request of a COMPLETED tape without a verified row;
    BROKEN while any request is unproven or missing, or a deep finding exists."""
    verified = len({row.request_id for row in certificates if row.verified})
    absent = [
        CertificateProblem(certificate_id, CertificateFinding.CERTIFICATE_MISSING)
        for _, certificate_id in missing
    ]
    failures = merge_certificate_problems(failed_certificates(certificates), deep, absent)
    broken = bool(unproven_requests(certificates)) or bool(deep) or bool(missing)
    return certificate_section(expected, verified, failures, broken)


def deep_findings_of(
    stored: Sequence[CertificateProblem], certificates: Sequence[Certificate]
) -> list[CertificateProblem]:
    """The certificate failures of a stored deep verification that are not failed rows held now.

    A stored report lists the failed rows (evidence) with the deep findings; only the
    latter are a deep verification's own result.
    """
    received = set(failed_certificates(certificates))
    return [problem for problem in stored if problem not in received]


def messages_section(reads: SectionReads, now: datetime, blind_after: timedelta) -> RegistryMessagesSection:
    """The quarantined messages, open unless applied late, and the outbox state (SPEC_DECISIONS 101)."""
    found = reads.read(
        ("registry_messages",),
        lambda unit: (
            unit.evidence.quarantine.all(),
            unit.outbox_ledger.applied_late(),
            unit.outbox_state.get(),
        ),
    )
    if found is None:
        return RegistryMessagesSection(reads.missing("registry_messages"), 0)
    held, late, state = found
    blind = state.blind(now, blind_after)
    since = state.blind_since
    if blind and since is None:  # no sync has run since the limit passed
        since = state.last_complete_sync_at + blind_after if state.last_complete_sync_at else None
    view = OutboxView(state.last_complete_sync_at, blind, since if blind else None, state.last_reason)
    return registry_messages_section(quarantine_findings(held, late), view)


def quarantine_findings(
    messages: Sequence[QuarantinedMessage], late: Set[uuid.UUID]
) -> list[QuarantineFinding]:
    """Each quarantined message as the report lists it (SPEC_DECISIONS 108)."""
    return [
        QuarantineFinding(
            m.quarantine_id, m.message_id, m.message_type, m.quarantined.reason.value, m.received_at,
            m.quarantined.withheld, m.message_id is None or m.message_id not in late,
        )
        for m in messages
    ]  # fmt: skip
