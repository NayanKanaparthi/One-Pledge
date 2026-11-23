"""`GetIntegrityReport`: what this bank has verified, now (SPEC 15.5, `GET /v1/integrity`).

- registry_chain: the stored checkpoints, plus the last deep verification's findings;
  BROKEN when a checkpoint failed or a deep finding exists, or when a registry request
  has an answer and no checkpoint is verified (SPEC_DECISIONS 89 a). NOT_VERIFIED only
  for a bank that has never had an answer from the registry.
- certificates: the answered requests, those with a verified certificate, every failed
  row ever stored (recovered ones marked), the last deep verification's findings and a
  CERTIFICATE_MISSING for every answered request of a COMPLETED tape without a verified
  row; BROKEN while any of these is open (SPEC_DECISIONS 61, 89 a).
- audit_log: the live entry count and last seq, with the last deep verification's
  result; BROKEN when it found a bad seq or lost the tail (SPEC_DECISIONS 89 c).
- raw_data: the last deep verification; BROKEN when it found a submission that does
  not verify.
- database_controls: live catalogue queries; BROKEN when any control is false.
- registry_messages: the quarantined outbox messages and the outbox state; BROKEN while a
  quarantined message is open (not applied late) or the bank is blind (SPEC_DECISIONS 101).
- evidence: the last deep verification's reconciliation (SPEC_DECISIONS 89 b).

audit_log, raw_data and evidence are NOT_VERIFIED before the first deep verification.
Each group of sections is read in its own transaction: when a query fails because the
store does not have the shape the code expects (a schema behind the code), those
sections are NOT_VERIFIED with the SQLSTATE in `unread` and the rest of the report is
still built (SPEC_DECISIONS 93, item 8).
"""

from __future__ import annotations

from datetime import timedelta

from onepledge_bank_agent.application.evidence_ports import DatabaseControlInspector, IntegrityRun
from onepledge_bank_agent.application.integrity_sections import (
    certificates_section,
    deep_findings_of,
    messages_section,
    registry_chain,
)
from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.application.section_reads import SectionReads
from onepledge_bank_agent.domain.integrity import (
    AuditChainSection,
    CertificateSection,
    IntegrityReport,
    RawDataSection,
    RegistryChainSection,
    broken_if,
    controls_section,
)
from onepledge_bank_agent.domain.integrity_document import StoredFindings, stored_findings
from onepledge_bank_agent.domain.store_errors import StoreUnreadableError

DEEP_SECTIONS = ("audit_log", "raw_data", "evidence")


class GetIntegrityReport:
    """Builds the live integrity report."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        controls: DatabaseControlInspector,
        runtime: Runtime,
        blind_after: timedelta,
    ) -> None:
        self._units = units
        self._controls = controls
        self._clock = runtime.clock
        self._blind_after = blind_after

    def execute(self) -> IntegrityReport:
        """The report as of now; no stored row can make it fail as a whole (SPEC_DECISIONS 100)."""
        reads = SectionReads(self._units)
        latest = reads.read(DEEP_SECTIONS, latest_run)
        run, stored = latest if latest is not None else (None, None)
        now = self._clock.now()
        return IntegrityReport(
            generated_at=now,
            last_deep_verification_at=run.ran_at if run is not None else None,
            registry_chain=self._chain(stored, reads),
            certificates=self._certificates(stored, reads),
            audit_log=self._audit(stored, reads),
            raw_data=stored.raw
            if stored is not None
            else RawDataSection(reads.missing("raw_data"), 0, 0, ()),
            database_controls=controls_section(self._controls.controls(), self._controls.violations()),
            registry_messages=messages_section(reads, now, self._blind_after),
            evidence=reads.evidence(stored.evidence if stored is not None else None),
            unread=tuple(reads.unread),
        )

    @staticmethod
    def _chain(stored: StoredFindings | None, reads: SectionReads) -> RegistryChainSection:
        found = reads.read(
            ("registry_chain",),
            lambda unit: (unit.evidence.checkpoints.all(), unit.evidence.certificates.expected()),
        )
        if found is None:
            return RegistryChainSection(reads.missing("registry_chain"), 0, 0, None, ())
        checkpoints, expected = found
        return registry_chain(checkpoints, stored.checkpoints if stored else (), expected > 0)

    @staticmethod
    def _certificates(stored: StoredFindings | None, reads: SectionReads) -> CertificateSection:
        found = reads.read(
            ("certificates",),
            lambda unit: (
                unit.evidence.certificates.expected(),
                unit.evidence.certificates.all(),
                unit.evidence.certificates.missing_for_completed(),
            ),
        )
        if found is None:
            return CertificateSection(reads.missing("certificates"), 0, 0, ())
        expected, certificates, missing = found
        deep = deep_findings_of(stored.certificates if stored else (), certificates)
        return certificates_section(expected, certificates, deep, missing)

    @staticmethod
    def _audit(stored: StoredFindings | None, reads: SectionReads) -> AuditChainSection:
        size = reads.read(("audit_log",), lambda unit: unit.history.audit_size())
        entries, last_seq = size if size is not None else (0, 0)
        if stored is None or size is None:
            return AuditChainSection(reads.missing("audit_log"), entries, last_seq, None)
        first_bad = stored.audit.first_bad_seq
        return AuditChainSection(broken_if(first_bad is not None), entries, last_seq, first_bad)

    @staticmethod
    def _raw(stored: StoredFindings | None, reads: SectionReads) -> RawDataSection:
        return stored.raw if stored is not None else RawDataSection(reads.missing("raw_data"), 0, 0, ())


def latest_run(unit: UnitOfWork) -> tuple[IntegrityRun, StoredFindings] | None:
    """The last deep verification and its findings; a stored report that cannot be read back is an
    unreadable `integrity_runs` row (SPEC_DECISIONS 100)."""
    run = unit.evidence.runs.latest()
    if run is None:
        return None
    try:
        return run, stored_findings(run.report)
    except (KeyError, TypeError, ValueError):
        raise StoreUnreadableError("integrity_runs", (str(run.run_id),)) from None
