"""The integrity report's text and JSON forms cannot differ (SPEC_DECISIONS 88; review 2, F5).

Both are rendered from one report object in which every section is BROKEN. Every section
name and every failure id must appear in both forms. The text lists every finding that
makes a section BROKEN under "Open failures" and a failure since recovered under
"Recovered (evidence kept)"; a recovered failure is never listed as open.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime

import pytest

from onepledge_bank_agent.domain.enums import CheckpointFailure, IntegrityRunStatus
from onepledge_bank_agent.domain.integrity import (
    AuditChainSection,
    CertificateFinding,
    CertificateProblem,
    CertificateSection,
    CheckpointProblem,
    ControlsSection,
    DatabaseControl,
    EvidenceProblem,
    EvidenceProblemKind,
    EvidenceSection,
    IntegrityReport,
    QuarantineFinding,
    RawDataSection,
    RegistryChainSection,
    RegistryMessagesSection,
    SectionStatus,
)
from onepledge_bank_agent.domain.integrity_document import all_findings
from onepledge_bank_agent.interface.cli.output import Output
from onepledge_bank_agent.interface.cli.registry_commands import FACTS, print_report

pytestmark = pytest.mark.unit

BROKEN = SectionStatus.BROKEN
ROLE_CONTROL = DatabaseControl.APP_ROLE_GRANTS_EXACT
NOW = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
IDS = [uuid.UUID(int=(index + 1) * 0x1111_1111_1111_1111) for index in range(8)]
(CHECKPOINT, OPEN_CERT, MISSING_CERT, RECOVERED_CERT, SUBMISSION, QUARANTINE, ROW, _) = IDS


def broken_report() -> IntegrityReport:
    """Every section BROKEN, each with the findings that make it so, and one recovered failure."""
    return IntegrityReport(
        NOW,
        NOW,
        RegistryChainSection(BROKEN, 3, 2, None, (
            CheckpointProblem(CHECKPOINT, 2, CheckpointFailure.HEAD_MISMATCH, 5, NOW),
        )),
        CertificateSection(BROKEN, 3, 1, (
            CertificateProblem(OPEN_CERT, CertificateFinding.BAD_SIGNATURE),
            CertificateProblem(MISSING_CERT, CertificateFinding.CERTIFICATE_MISSING),
            CertificateProblem(RECOVERED_CERT, CertificateFinding.BODY_HASH_MISMATCH, recovered=True),
        )),
        AuditChainSection(BROKEN, 40, 41, 17),
        RawDataSection(BROKEN, 2, 30, (SUBMISSION,)),
        ControlsSection(
            BROKEN,
            tuple((control, control is not ROLE_CONTROL) for control in DatabaseControl),
            ("ROLE_HAS_EXTRA_GRANT on loans_raw",),
        ),
        RegistryMessagesSection(BROKEN, 1, (QuarantineFinding(QUARANTINE, "ALERT", "SCHEMA_MISMATCH"),)),
        EvidenceSection(BROKEN, (
            EvidenceProblem("certificates", str(ROW), EvidenceProblemKind.ROW_MISSING),
            EvidenceProblem("checkpoints_seen", "9", EvidenceProblemKind.SEQUENCE_GAP),
        )),
    )  # fmt: skip


def rendered(report: IntegrityReport, *, as_json: bool) -> tuple[str, int]:
    out = io.StringIO()
    code = print_report(report, Output(out, io.StringIO(), as_json=as_json), "Integrity report")
    return out.getvalue(), code


def section_of(text: str, title: str) -> list[str]:
    """The lines of one titled table of the text report, up to its count line."""
    lines = text.splitlines()
    start = lines.index(title) + 1
    end = next(i for i in range(start, len(lines)) if not lines[i].startswith("  "))
    return lines[start:end]


FAILURE_IDS = [
    str(CHECKPOINT), str(OPEN_CERT), str(MISSING_CERT), str(RECOVERED_CERT), "17", str(SUBMISSION),
    DatabaseControl.APP_ROLE_GRANTS_EXACT.value, str(ROW), "9",
]  # fmt: skip
"""Every failure id the JSON document carries."""
TEXT_ONLY = [str(QUARANTINE), "ROLE_HAS_EXTRA_GRANT"]
"""Findings the contract's `RegistryMessagesSection` and `DatabaseControlsSection` have no field for
(`additionalProperties: false`); the text report and the report object carry them."""


def test_every_section_and_every_failure_id_is_in_both_forms() -> None:
    report = broken_report()
    text, text_code = rendered(report, as_json=False)
    document, json_code = rendered(report, as_json=True)
    parsed = json.loads(document)
    sections = [name for name, value in parsed.items() if isinstance(value, dict) and "status" in value]
    assert sections == list(FACTS) == list(report.section_statuses())
    assert len(sections) == 7
    table = section_of(text, "Sections")
    for name in sections:
        assert parsed[name]["status"] == "BROKEN"
        assert any(line.split()[:2] == [name, "BROKEN"] for line in table), name
    for ident in FAILURE_IDS:
        assert ident in document, ident
        assert ident in text, ident
    for ident in TEXT_ONLY:
        assert ident in text, ident
    assert (report.status, text_code, json_code) == (IntegrityRunStatus.BROKEN, 1, 1)
    assert all(len(line) <= 100 for line in text.splitlines()), "SPEC_DECISIONS 78: the text form fits"


def test_open_and_recovered_failures_are_listed_apart_and_every_open_one_is_listed() -> None:
    report = broken_report()
    text, _ = rendered(report, as_json=False)
    open_lines = "\n".join(section_of(text, "Open failures"))
    recovered_lines = "\n".join(section_of(text, "Recovered (evidence kept)"))
    open_findings, recovered = all_findings(report)
    assert {f.section for f in open_findings} == set(report.section_statuses())
    for finding in open_findings:
        assert finding.ident in open_lines and finding.reason in open_lines, finding
    assert [f.ident for f in recovered] == [str(RECOVERED_CERT)]
    assert str(RECOVERED_CERT) in recovered_lines
    assert str(RECOVERED_CERT) not in open_lines
    for ident in (str(QUARANTINE), str(MISSING_CERT), "CERTIFICATE_MISSING", "SCHEMA_MISMATCH"):
        assert ident in open_lines, ident


def test_an_ok_report_lists_no_failures_and_exits_zero() -> None:
    report = broken_report()
    ok = IntegrityReport(
        NOW, NOW,
        RegistryChainSection(SectionStatus.OK, 0, 0, None, ()),
        CertificateSection(SectionStatus.OK, 0, 0, ()),
        AuditChainSection(SectionStatus.OK, 40, 40, None),
        RawDataSection(SectionStatus.OK, 2, 30, ()),
        ControlsSection(SectionStatus.OK, tuple((c, True) for c, _ in report.database_controls.controls)),
        RegistryMessagesSection(SectionStatus.OK, 0),
        EvidenceSection(SectionStatus.OK, ()),
    )  # fmt: skip
    text, code = rendered(ok, as_json=False)
    assert (ok.status, code) == (IntegrityRunStatus.OK, 0)
    assert "Open failures" not in text and "Recovered (evidence kept)" not in text
    assert all_findings(ok) == ([], [])
