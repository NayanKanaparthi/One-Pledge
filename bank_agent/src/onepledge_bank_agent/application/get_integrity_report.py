"""`GetIntegrityReport`: what this bank has verified, now (SPEC 15.5, `GET /v1/integrity`).

- registry_chain: the stored checkpoints, plus the last deep verification's findings;
  BROKEN when a checkpoint failed or a deep finding exists.
- certificates: the stored certificates, plus the last deep verification's findings;
  BROKEN when a certificate failed or a deep finding exists.
- audit_log: the live entry count and last seq, with the last deep verification's
  result; BROKEN when it found a bad seq.
- raw_data: the last deep verification; BROKEN when it found a submission that does
  not verify.
- database_controls: live catalogue queries; BROKEN when any control is false.

audit_log and raw_data are NOT_VERIFIED before the first deep verification;
registry_chain is NOT_VERIFIED before the first checkpoint.
"""

from __future__ import annotations

from onepledge_bank_agent.application.evidence_ports import DatabaseControlInspector
from onepledge_bank_agent.application.integrity_sections import failed_certificates, registry_chain
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.integrity import (
    AuditChainSection,
    CertificateProblem,
    CheckpointProblem,
    IntegrityReport,
    RawDataSection,
    SectionStatus,
    broken_if,
    certificate_section,
    controls_section,
    merge_certificate_problems,
    stored_findings,
)

NOT_VERIFIED_RAW = RawDataSection(SectionStatus.NOT_VERIFIED, 0, 0, ())


class GetIntegrityReport:
    """Builds the live integrity report."""

    def __init__(
        self, units: UnitOfWorkFactory, controls: DatabaseControlInspector, runtime: Runtime
    ) -> None:
        self._units = units
        self._controls = controls
        self._clock = runtime.clock

    def execute(self) -> IntegrityReport:
        """The report as of now."""
        with self._units() as unit:
            checkpoints = unit.checkpoints.all()
            certificates = unit.certificates.all()
            entries, last_seq = unit.evidence.audit_size()
            run = unit.integrity_runs.latest()
        deep_checkpoints: tuple[CheckpointProblem, ...] = ()
        deep_certificates: tuple[CertificateProblem, ...] = ()
        if run is None:
            audit = AuditChainSection(SectionStatus.NOT_VERIFIED, entries, last_seq, None)
            raw = NOT_VERIFIED_RAW
        else:
            deep_checkpoints, deep_certificates, last_audit, raw = stored_findings(run.report)
            first_bad = last_audit.first_bad_seq
            audit = AuditChainSection(broken_if(first_bad is not None), entries, last_seq, first_bad)
        return IntegrityReport(
            generated_at=self._clock.now(),
            last_deep_verification_at=run.ran_at if run is not None else None,
            registry_chain=registry_chain(checkpoints, deep_checkpoints),
            certificates=certificate_section(
                len(certificates),
                merge_certificate_problems(failed_certificates(certificates), deep_certificates),
            ),
            audit_log=audit,
            raw_data=raw,
            database_controls=controls_section(self._controls.controls()),
        )
