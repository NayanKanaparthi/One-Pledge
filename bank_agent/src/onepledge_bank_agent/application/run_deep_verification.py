"""`RunDeepVerification`: recompute every proof from stored data, trusting no stored flag (SPEC 15.6).

1. Audit log: seqs gapless from 1, each link and each entry hash recomputed.
2. Raw data: every submission's stored forms agree with each other and with its file
   hash (SPEC_DECISIONS 25 rule 6).
3. Certificates: for every stored row, `delivered_body_sha256` recomputed from the stored
   body (SPEC_DECISIONS 64), so an edited body is found even if `body_hash` was edited
   too; then key id, body hash and signature of every stored verified certificate (a
   failed row is already reported as failed and stays as evidence).
4. Checkpoints: every stored verified signature, then the whole ledger chain from seq 1
   to the highest stored checkpoint, and every verified checkpoint's head against it.
5. Database controls, live; quarantined registry messages, counted.

The event headers are read first, with nothing open: if the registry cannot be reached,
the error is raised and nothing is stored. Otherwise the report is stored in
`integrity_runs` and audited as `INTEGRITY_VERIFICATION_RUN` with the status per section.
"""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.evidence_checks import audit_tail, evidence_problems, tail_missing
from onepledge_bank_agent.application.evidence_ports import DatabaseControlInspector, IntegrityRun
from onepledge_bank_agent.application.integrity_sections import (
    certificates_section,
    deep_checkpoint_problems,
    quarantine_findings,
    registry_chain,
)
from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.registry_ports import SignatureVerifier
from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.application.verify_checkpoint import EventHeaderReader
from onepledge_bank_agent.domain.audit import AuditDraft, AuditEntry, first_broken_seq
from onepledge_bank_agent.domain.certificate_rules import delivered_sha256, verify_proof
from onepledge_bank_agent.domain.checkpoint_rules import deep_findings, ledger_end
from onepledge_bank_agent.domain.entities import Certificate
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, AuditOutcome
from onepledge_bank_agent.domain.integrity import (
    AuditChainSection,
    CertificateFinding,
    CertificateProblem,
    IntegrityReport,
    RawDataSection,
    broken_if,
    controls_section,
    evidence_section,
    registry_messages_section,
)
from onepledge_bank_agent.domain.integrity_document import AuditTail, stored_document, stored_findings
from onepledge_bank_agent.domain.raw_evidence import tape_is_intact


def certificate_finding(row: Certificate, verifier: SignatureVerifier) -> CertificateProblem | None:
    """What deep verification finds wrong with one stored certificate row, if anything."""
    recomputed = delivered_sha256(row.body)
    if recomputed != row.delivered_body_sha256.value:
        return CertificateProblem(row.certificate_id, CertificateFinding.BODY_HASH_MISMATCH)
    if not row.verified:
        return None
    failure = verify_proof(row.as_received(), recomputed, verifier)
    return (
        None if failure is None else CertificateProblem(row.certificate_id, CertificateFinding(failure.value))
    )


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
            checkpoints = unit.evidence.checkpoints.all()
        events = self._sources.headers.read(0, ledger_end(checkpoints))
        findings = deep_findings(checkpoints, events, self._sources.verifier)
        with self._units() as unit:
            entries = list(unit.history.audit_entries())
            previous = unit.evidence.runs.latest()
            audit = self._audit_chain(unit, entries, previous)
            raw = self._raw_data(unit)
            certificates = unit.evidence.certificates.all()
            expected = unit.evidence.certificates.expected()
            missing = unit.evidence.certificates.missing_for_completed()
            quarantined = unit.evidence.quarantine.all()
            problems = evidence_problems(unit, entries)
        verifier = self._sources.verifier
        broken_proofs = [
            problem for row in certificates if (problem := certificate_finding(row, verifier)) is not None
        ]
        now = self._clock.now()
        report = IntegrityReport(
            generated_at=now,
            last_deep_verification_at=now,
            registry_chain=registry_chain(checkpoints, deep_checkpoint_problems(findings), expected > 0),
            certificates=certificates_section(expected, certificates, broken_proofs, missing),
            audit_log=audit,
            raw_data=raw,
            database_controls=controls_section(
                self._sources.controls.controls(), self._sources.controls.violations()
            ),
            registry_messages=registry_messages_section(quarantine_findings(quarantined)),
            evidence=evidence_section(problems),
        )
        self._store(report, requester, audit_tail(entries))
        return report

    @staticmethod
    def _audit_chain(
        unit: UnitOfWork, entries: list[AuditEntry], previous: IntegrityRun | None
    ) -> AuditChainSection:
        """The chain rule, and the tail the previous deep verification saw (SPEC_DECISIONS 89 c)."""
        count, last_seq = unit.history.audit_size()
        first_bad = first_broken_seq(entries)
        if first_bad is None and previous is not None:
            first_bad = tail_missing(stored_findings(previous.report).tail, entries)
        return AuditChainSection(broken_if(first_bad is not None), count, last_seq, first_bad)

    @staticmethod
    def _raw_data(unit: UnitOfWork) -> RawDataSection:
        bad = []
        submissions = rows = 0
        for submission_id in unit.history.submission_ids():
            tape = unit.history.tape(submission_id)
            submissions += 1
            if tape is None or not tape_is_intact(tape):
                bad.append(submission_id)
            rows += len(tape.rows) if tape is not None else 0
        return RawDataSection(broken_if(bool(bad)), submissions, rows, tuple(bad))

    def _store(self, report: IntegrityReport, requester: Requester, tail: AuditTail | None) -> None:
        run = IntegrityRun(
            self._ids.new_id(),
            report.generated_at,
            requester.actor.user_id,
            report.status,
            stored_document(report, tail),
        )
        with self._units() as unit:
            unit.evidence.runs.add(run)
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
