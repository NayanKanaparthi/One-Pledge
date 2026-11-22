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
- registry_messages: the quarantined outbox messages; BROKEN when any exists.
- evidence: the last deep verification's reconciliation (SPEC_DECISIONS 89 b).

audit_log, raw_data and evidence are NOT_VERIFIED before the first deep verification.
Each group of sections is read in its own transaction: when a query fails because the
store does not have the shape the code expects (a schema behind the code), those
sections are NOT_VERIFIED with the SQLSTATE in `unread` and the rest of the report is
still built (SPEC_DECISIONS 93, item 8).
"""

from __future__ import annotations

from collections.abc import Callable

from onepledge_bank_agent.application.evidence_ports import DatabaseControlInspector
from onepledge_bank_agent.application.integrity_sections import (
    certificates_section,
    deep_findings_of,
    quarantine_findings,
    registry_chain,
)
from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.integrity import (
    AuditChainSection,
    CertificateSection,
    IntegrityReport,
    RawDataSection,
    RegistryChainSection,
    RegistryMessagesSection,
    SectionStatus,
    UnreadSection,
    broken_if,
    controls_section,
    evidence_section,
    registry_messages_section,
)
from onepledge_bank_agent.domain.integrity_document import StoredFindings, stored_findings
from onepledge_bank_agent.domain.store_errors import StoreMismatchError

NOT_VERIFIED = SectionStatus.NOT_VERIFIED
NOT_VERIFIED_RAW = RawDataSection(NOT_VERIFIED, 0, 0, ())
DEEP_SECTIONS = ("audit_log", "raw_data", "evidence")


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
        unread: list[UnreadSection] = []
        run = self._read(DEEP_SECTIONS, lambda unit: unit.evidence.runs.latest(), unread)
        stored = stored_findings(run.report) if run is not None else None
        return IntegrityReport(
            generated_at=self._clock.now(),
            last_deep_verification_at=run.ran_at if run is not None else None,
            registry_chain=self._chain(stored, unread),
            certificates=self._certificates(stored, unread),
            audit_log=self._audit(stored, unread),
            raw_data=stored.raw if stored is not None else NOT_VERIFIED_RAW,
            database_controls=controls_section(self._controls.controls(), self._controls.violations()),
            registry_messages=self._messages(unread),
            evidence=evidence_section(stored.evidence if stored is not None else None),
            unread=tuple(unread),
        )

    def _read[T](
        self, sections: tuple[str, ...], query: Callable[[UnitOfWork], T], unread: list[UnreadSection]
    ) -> T | None:
        """`query` in its own transaction, or None with each section recorded as unread."""
        try:
            with self._units() as unit:
                return query(unit)
        except StoreMismatchError as error:
            unread += [UnreadSection(section, error.sqlstate) for section in sections]
            return None

    def _chain(self, stored: StoredFindings | None, unread: list[UnreadSection]) -> RegistryChainSection:
        found = self._read(
            ("registry_chain",),
            lambda unit: (unit.evidence.checkpoints.all(), unit.evidence.certificates.expected()),
            unread,
        )
        if found is None:
            return RegistryChainSection(NOT_VERIFIED, 0, 0, None, ())
        checkpoints, expected = found
        return registry_chain(checkpoints, stored.checkpoints if stored else (), expected > 0)

    def _certificates(self, stored: StoredFindings | None, unread: list[UnreadSection]) -> CertificateSection:
        found = self._read(
            ("certificates",),
            lambda unit: (
                unit.evidence.certificates.expected(),
                unit.evidence.certificates.all(),
                unit.evidence.certificates.missing_for_completed(),
            ),
            unread,
        )
        if found is None:
            return CertificateSection(NOT_VERIFIED, 0, 0, ())
        expected, certificates, missing = found
        deep = deep_findings_of(stored.certificates if stored else (), certificates)
        return certificates_section(expected, certificates, deep, missing)

    def _audit(self, stored: StoredFindings | None, unread: list[UnreadSection]) -> AuditChainSection:
        size = self._read(("audit_log",), lambda unit: unit.history.audit_size(), unread)
        entries, last_seq = size if size is not None else (0, 0)
        if stored is None or size is None:
            return AuditChainSection(NOT_VERIFIED, entries, last_seq, None)
        first_bad = stored.audit.first_bad_seq
        return AuditChainSection(broken_if(first_bad is not None), entries, last_seq, first_bad)

    def _messages(self, unread: list[UnreadSection]) -> RegistryMessagesSection:
        held = self._read(("registry_messages",), lambda unit: unit.evidence.quarantine.all(), unread)
        if held is None:
            return RegistryMessagesSection(NOT_VERIFIED, 0)
        return registry_messages_section(quarantine_findings(held))
