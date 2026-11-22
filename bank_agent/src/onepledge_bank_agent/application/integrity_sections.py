"""Building the sections of the integrity report from stored rows (SPEC 15.5), shared by both use cases."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from onepledge_bank_agent.domain.checkpoint_rules import DeepFinding
from onepledge_bank_agent.domain.entities import Certificate, Checkpoint
from onepledge_bank_agent.domain.integrity import (
    CertificateProblem,
    CertificateSection,
    CheckpointProblem,
    CheckpointRef,
    RegistryChainSection,
    certificate_section,
    chain_section,
    merge_certificate_problems,
    merge_checkpoint_problems,
)


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
    checkpoints: Sequence[Checkpoint], extra: Sequence[CheckpointProblem]
) -> RegistryChainSection:
    """Counts, the last verified checkpoint, and the stored failures together with `extra` ones."""
    failures = merge_checkpoint_problems(failed_checkpoints(checkpoints), extra)
    verified = sum(1 for row in checkpoints if row.verified)
    return chain_section(len(checkpoints), verified, last_verified(checkpoints), failures)


def failed_certificates(certificates: Sequence[Certificate]) -> list[CertificateProblem]:
    """Every stored row that failed verification when it was received (kept as evidence)."""
    return [
        CertificateProblem(row.certificate_id, row.failure) for row in certificates if row.failure is not None
    ]


def unproven_requests(certificates: Sequence[Certificate]) -> set[uuid.UUID]:
    """Requests with a failed row and no verified one: the bank holds no valid proof of them."""
    proven = {row.request_id for row in certificates if row.verified}
    return {row.request_id for row in certificates if not row.verified} - proven


def certificates_section(
    expected: int, certificates: Sequence[Certificate], deep: Sequence[CertificateProblem]
) -> CertificateSection:
    """SPEC_DECISIONS 61: `total` is the requests that should have a certificate, `verified` those
    with a verified row, `failures` every failed row ever stored plus the deep findings; BROKEN
    only while a request is unproven or a deep finding exists."""
    verified = len({row.request_id for row in certificates if row.verified})
    failures = merge_certificate_problems(failed_certificates(certificates), deep)
    broken = bool(unproven_requests(certificates)) or bool(deep)
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
