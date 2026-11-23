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
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from bank_testkit.data import CONTRACTS
from onepledge_bank_agent.domain.enums import CheckpointFailure, IntegrityRunStatus, TapeStatus
from onepledge_bank_agent.domain.integrity import (
    AuditChainSection,
    CertificateFinding,
    CertificateProblem,
    CertificateSection,
    CheckpointProblem,
    ControlsSection,
    ControlViolation,
    DatabaseControl,
    EvidenceProblem,
    EvidenceProblemKind,
    EvidenceSection,
    IntegrityReport,
    OutboxView,
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
(CHECKPOINT, OPEN_CERT, MISSING_CERT, RECOVERED_CERT, SUBMISSION, QUARANTINE, ROW, CLOSED) = IDS
MANIFEST, SESSION = DatabaseControl.SCHEMA_MATCHES_MANIFEST, DatabaseControl.TRIGGERS_FIRE_FOR_APP_ROLE
VIOLATIONS = (
    ControlViolation(ROLE_CONTROL, "ROLE_HAS_EXTRA_GRANT", "loans_raw"),
    ControlViolation(MANIFEST, "SCHEMA_OBJECT_ADDED", "trigger loans.optest_extra"),
    ControlViolation(SESSION, "ROLE_HAS_SETTING", "search_path"),
    ControlViolation(ROLE_CONTROL, "ROLE_CAN_LOGIN_ELSEWHERE", None),
)
MESSAGES = (
    QuarantineFinding(QUARANTINE, QUARANTINE, "ALERT", "SCHEMA_MISMATCH", NOW, False, True),
    QuarantineFinding(
        CLOSED, None, "CASE_UPDATE", "VALUE_OUT_OF_RANGE", NOW - timedelta(hours=1), True, False
    ),
)


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
            tuple((control, control not in {ROLE_CONTROL, MANIFEST, SESSION}) for control in DatabaseControl),
            VIOLATIONS,
        ),
        RegistryMessagesSection(BROKEN, 2, MESSAGES, OutboxView(NOW, True, NOW, "STORE_UNAVAILABLE")),
        EvidenceSection(BROKEN, (
            EvidenceProblem("certificates", str(ROW), EvidenceProblemKind.ROW_MISSING),
            EvidenceProblem("checkpoints_seen", "9", EvidenceProblemKind.SEQUENCE_GAP),
            EvidenceProblem("local_checks", str(SUBMISSION), EvidenceProblemKind.DIGEST_MISMATCH,
                            TapeStatus.CHECKED),
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
    DatabaseControl.APP_ROLE_GRANTS_EXACT.value, str(ROW), "9", str(QUARANTINE), str(CLOSED),
    "ROLE_HAS_EXTRA_GRANT", "SCHEMA_OBJECT_ADDED", "ROLE_HAS_SETTING",
]  # fmt: skip
"""Failure ids and codes both forms carry."""


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
    assert [f.ident for f in recovered] == [str(RECOVERED_CERT), str(CLOSED)]
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


type Row = tuple[str, str, str, str]
"""One finding as both forms state it: section, id, reason, and the other facts."""


def text_findings(text: str) -> list[Row]:
    """Every finding of "Open failures" and "Recovered (evidence kept)", continuation lines joined."""
    found: list[Row] = []
    for title in ("Open failures", "Recovered (evidence kept)"):
        if title not in text:
            continue
        lines = text.splitlines()
        header = lines[lines.index(title) + 1]
        column = header.index("finding")
        cells: list[tuple[str, str]] = []
        for line in section_of(text, title)[1:]:
            if line[:column].strip():
                cells.append((line.split()[0], line[column:]))
            else:
                cells[-1] = (cells[-1][0], f"{cells[-1][1]} {line[column:]}")
        for section, cell in cells:
            first, _, rest = cell.partition(", ")
            ident, _, reason = first.rpartition(": ")
            found.append((section, ident, reason, rest))
    return found


def json_findings(document: dict[str, Any]) -> list[Row]:
    """Every finding the JSON document carries, read from its fields alone."""
    found: list[Row] = []
    for p in document["registry_chain"]["failures"]:
        bad = p["first_bad_event_seq"]
        where = f"seq {p['checkpoint_seq']}" + (f", first bad event {bad}" if bad is not None else "")
        found.append(("registry_chain", p["checkpoint_id"], p["failure_reason"], where))
    found += [("certificates", p["certificate_id"], p["failure_reason"], "")
              for p in document["certificates"]["failures"]]  # fmt: skip
    if document["audit_log"]["first_bad_seq"] is not None:
        found.append(("audit_log", str(document["audit_log"]["first_bad_seq"]), "CHAIN_BREAK", ""))
    found += [
        ("raw_data", ident, "RAW_DATA_MISMATCH", "") for ident in document["raw_data"]["bad_submission_ids"]
    ]
    controls = document["database_controls"]
    found += [
        ("database_controls", c["name"], "NOT_IN_FORCE", "") for c in controls["controls"] if not c["ok"]
    ]
    found += [("database_controls", v["object"] or "", v["code"], "") for v in controls["violations"]]
    messages = document["registry_messages"]
    found += [("registry_messages", m["quarantine_id"], m["reason"], m["message_type"])
              for m in messages["messages"]]  # fmt: skip
    outbox = messages["outbox"]
    if outbox["blind"]:
        since = f"since {outbox['blind_since'] or 'the start'}"
        found.append(
            (
                "registry_messages",
                "",
                "OUTBOX_BLIND",
                since + (f", {outbox['reason']}" if outbox["reason"] else ""),
            )
        )
    found += [("evidence", p["row_id"] or "", p["problem"], f"stage {p['stage']}" if p["stage"] else "")
              for p in document["evidence"]["problems"]]  # fmt: skip
    return found


def validator() -> Draft202012Validator:
    contract = yaml.safe_load((CONTRACTS / "bank_agent.yaml").read_text(encoding="utf-8"))
    schema = {"$ref": "#/components/schemas/IntegrityReport", "components": contract["components"]}
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def both_forms(report: IntegrityReport) -> tuple[str, dict[str, Any]]:
    text, _ = rendered(report, as_json=False)
    document = json.loads(rendered(report, as_json=True)[0])
    assert document.pop("ok") is True
    assert [error.message for error in validator().iter_errors(document)] == []
    return text, document


def test_every_finding_in_the_text_is_in_the_json_and_every_finding_in_the_json_is_in_the_text() -> None:
    """SPEC_DECISIONS 108 and 99: both directions, with no list of exemptions (review 3, F4)."""
    text, document = both_forms(broken_report())
    in_text, in_json = text_findings(text), json_findings(document)
    assert len(in_text) == len(set(in_text)) and len(in_json) == len(set(in_json))
    assert sorted(in_text) == sorted(in_json)
    assert ("database_controls", "trigger loans.optest_extra", "SCHEMA_OBJECT_ADDED", "") in in_json
    assert ("evidence", str(SUBMISSION), "DIGEST_MISMATCH", "stage CHECKED") in in_json
    assert (
        "registry_messages",
        "",
        "OUTBOX_BLIND",
        "since 2026-11-20T09:30:00Z, STORE_UNAVAILABLE",
    ) in in_text
    facts = "\n".join(section_of(text, "Sections"))
    assert "4 violations" in facts and "outbox blind since 2026-11-20T09:30:00Z" in facts


def test_long_lists_are_capped_at_200_newest_first_in_both_forms_with_their_totals() -> None:
    report = broken_report()
    many = 250
    messages = tuple(
        QuarantineFinding(
            uuid.UUID(int=n), None, "ALERT", "SCHEMA_MISMATCH", NOW - timedelta(minutes=n), False, True
        )
        for n in range(1, many + 1)
    )
    capped = replace(
        report,
        database_controls=replace(report.database_controls, violations=tuple(
            ControlViolation(ROLE_CONTROL, "ROLE_HAS_EXTRA_GRANT", f"table_{n}") for n in range(many)
        )),
        registry_messages=replace(report.registry_messages, quarantined=many, messages=messages),
        evidence=replace(report.evidence, problems=tuple(
            EvidenceProblem("loans", str(uuid.UUID(int=n)), EvidenceProblemKind.DERIVATION_MISMATCH)
            for n in range(many)
        )),
    )  # fmt: skip
    text, document = both_forms(capped)
    assert sorted(text_findings(text)) == sorted(json_findings(document))
    controls, messages_json = document["database_controls"], document["registry_messages"]
    assert (len(controls["violations"]), controls["violations_total"]) == (200, many)
    assert (len(messages_json["messages"]), messages_json["quarantined"]) == (200, many)
    assert (len(document["evidence"]["problems"]), document["evidence"]["problems_total"]) == (200, many)
    received = [m["received_at"] for m in messages_json["messages"]]
    assert received == sorted(received, reverse=True) and messages_json["messages"][0][
        "quarantine_id"
    ] == str(uuid.UUID(int=1)), "newest first"
    facts = "\n".join(section_of(text, "Sections"))
    assert facts.count("first 200 listed") == 3
