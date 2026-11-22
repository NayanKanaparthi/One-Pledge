"""`RunDeepVerification`: recompute every proof from stored data, trusting no stored flag (SPEC 15.6).

1. Audit log: seqs gapless from 1, each link and each entry hash recomputed.
2. Raw data: every submission's stored forms agree with each other and with its file
   hash (SPEC_DECISIONS 25 rule 6).
3. Certificates: key id, body hash and signature of every stored certificate.
4. Checkpoints: every stored verified signature, then the whole ledger chain from seq 1
   to the highest stored checkpoint, and every verified checkpoint's head against it.
5. Database controls, live.

The event headers are read first, with nothing open: if the registry cannot be reached,
the error is raised and nothing is stored. Otherwise the report is stored in
`integrity_runs` and audited as `INTEGRITY_VERIFICATION_RUN` with the status per section.
"""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.evidence_ports import DatabaseControlInspector, IntegrityRun
from onepledge_bank_agent.application.integrity_sections import (
    deep_checkpoint_problems,
    failed_certificates,
    registry_chain,
)
from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.registry_ports import SignatureVerifier
from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.application.verify_checkpoint import EventHeaderReader
from onepledge_bank_agent.domain.audit import AuditDraft, first_broken_seq
from onepledge_bank_agent.domain.certificate_rules import verify_proof
from onepledge_bank_agent.domain.checkpoint_rules import deep_findings, ledger_end
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, AuditOutcome
from onepledge_bank_agent.domain.integrity import (
    AuditChainSection,
    CertificateProblem,
    IntegrityReport,
    RawDataSection,
    broken_if,
    certificate_section,
    controls_section,
    merge_certificate_problems,
    report_json,
)
from onepledge_bank_agent.domain.raw_evidence import tape_is_intact


@dataclass(frozen=True)
class VerificationSources:
    """Where deep verification reads what it cannot find in the bank database."""

    headers: EventHeaderReader
    verifier: SignatureVerifier
    controls: DatabaseControlInspector


class RunDeepVerification:
    """Runs one deep verification and stores its report."""

    def __init__(self, units: UnitOfWorkFactory, sources: VerificationSources, runtime: Runtime) -> None:
        self._units = units
        self._sources = sources
        self._clock = runtime.clock
        self._ids = runtime.ids
        self._log = runtime.log

    def execute(self, requester: Requester) -> IntegrityReport:
        """The fresh report, stored and audited."""
        with self._units() as unit:
            checkpoints = unit.checkpoints.all()
        events = self._sources.headers.read(0, ledger_end(checkpoints))
        findings = deep_findings(checkpoints, events, self._sources.verifier)
        with self._units() as unit:
            audit = self._audit_chain(unit)
            raw = self._raw_data(unit)
            certificates = unit.certificates.all()
        verifier = self._sources.verifier
        broken_proofs = [
            CertificateProblem(row.certificate_id, failure)
            for row in certificates
            if (failure := verify_proof(row.as_received(), verifier)) is not None
        ]
        now = self._clock.now()
        report = IntegrityReport(
            generated_at=now,
            last_deep_verification_at=now,
            registry_chain=registry_chain(checkpoints, deep_checkpoint_problems(findings)),
            certificates=certificate_section(
                len(certificates),
                merge_certificate_problems(failed_certificates(certificates), broken_proofs),
            ),
            audit_log=audit,
            raw_data=raw,
            database_controls=controls_section(self._sources.controls.controls()),
        )
        self._store(report, requester)
        return report

    @staticmethod
    def _audit_chain(unit: UnitOfWork) -> AuditChainSection:
        entries, last_seq = unit.evidence.audit_size()
        first_bad = first_broken_seq(unit.evidence.audit_entries())
        return AuditChainSection(broken_if(first_bad is not None), entries, last_seq, first_bad)

    @staticmethod
    def _raw_data(unit: UnitOfWork) -> RawDataSection:
        bad = []
        submissions = rows = 0
        for submission_id in unit.evidence.submission_ids():
            tape = unit.evidence.tape(submission_id)
            submissions += 1
            if tape is None or not tape_is_intact(tape):
                bad.append(submission_id)
            rows += len(tape.rows) if tape is not None else 0
        return RawDataSection(broken_if(bool(bad)), submissions, rows, tuple(bad))

    def _store(self, report: IntegrityReport, requester: Requester) -> None:
        run = IntegrityRun(
            self._ids.new_id(),
            report.generated_at,
            requester.actor.user_id,
            report.status,
            report_json(report),
        )
        with self._units() as unit:
            unit.integrity_runs.add(run)
            unit.record(
                AuditDraft(
                    AuditAction.INTEGRITY_VERIFICATION_RUN,
                    requester.actor,
                    AuditObjectType.INTEGRITY_RUN,
                    run.run_id,
                    AuditOutcome.SUCCESS,
                    {"status": report.status.value, **report.section_statuses(), **requester.detail()},
                    requester.request_id,
                )
            )
            unit.commit()
        self._log.info(LogEvent.INTEGRITY_VERIFIED, status=report.status.value)
