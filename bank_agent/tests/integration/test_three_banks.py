"""WORK_PACKAGES WP3: every step of the `small` data set for all three banks, in process.

Three bank databases, one registry and one pair of key holders (the real doubles) and
the real outside records service; tapes fed through `IngestTape` in manifest order,
S14 and S15 (the RACE group) uploaded together and processed by two workers at once.
Asserted without the answer key, then each registry tamper mode must turn the
integrity report BROKEN with the right reason.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest

from bank_testkit.banks import ThreeBanks
from bank_testkit.database import BankDatabase
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.world import Faults
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.domain.enums import CertificateFailure, CheckpointFailure, IntegrityRunStatus
from onepledge_bank_agent.domain.integrity import IntegrityReport, SectionStatus

pytestmark = pytest.mark.integration


def scalar(database: BankDatabase, query: str, parameters: tuple[object, ...] = ()) -> object:
    with database.connect("admin") as connection:
        row = connection.execute(query, parameters).fetchone()
    assert row is not None
    return row[0]


def rows(database: BankDatabase, query: str, parameters: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with database.connect("admin") as connection:
        return connection.execute(query, parameters).fetchall()


@pytest.fixture
def banks(
    new_database: Callable[[], BankDatabase], small_data: Path, small_outside: InProcessOutsideRecords
) -> Iterator[ThreeBanks]:
    with ThreeBanks(new_database, small_data, small_outside) as world:
        world.replay()
        yield world


def chain_reasons(report: IntegrityReport) -> set[tuple[CheckpointFailure, int | None]]:
    return {(f.failure_reason, f.first_bad_event_seq) for f in report.registry_chain.failures}


def test_the_small_replay_agrees_with_the_registry_and_every_integrity_section_is_ok(
    banks: ThreeBanks,
) -> None:
    cases = banks.doubles.control("http://registry.test", "GET", "/_test/dump")["cases"]
    assert cases, "the small data set plants conflicts"
    member_of = {member.member_id: code for code, member in banks.doubles.members.items()}
    for code, database in banks.databases.items():
        assert scalar(database, "SELECT count(*) FROM loans WHERE state = 'PENDING'") == 0, code
        assert scalar(database, "SELECT count(*) FROM tape_submissions WHERE status <> 'COMPLETED'") == 0, (
            code
        )
        locked = scalar(database, "SELECT count(*) FROM loans WHERE state = 'LOCKED'")
        assert (
            locked
            == len(banks.doubles.active_locks(code))
            == banks.doubles.member_stats(code)["active_locks"]
        )
        unverified = (
            "SELECT count(*) FROM registry_requests r WHERE response_status IN (200, 201) AND NOT EXISTS "
            "(SELECT 1 FROM certificates c WHERE c.request_id = r.request_id AND c.verified)"
        )
        assert scalar(database, unverified) == 0, code
        disagreeing = (
            "SELECT count(*) FROM loans l WHERE state IN ('LOCKED', 'CONFLICT', 'RELEASED') AND NOT EXISTS "
            "(SELECT 1 FROM registry_request_items i WHERE i.loan_id = l.loan_id AND i.result = CASE l.state "
            "WHEN 'CONFLICT' THEN 'CONFLICT' WHEN 'RELEASED' THEN 'RELEASED' ELSE 'LOCKED' END)"
        )
        assert scalar(database, disagreeing) == 0, code
        others = [m.member_id for c, m in banks.doubles.members.items() if c != code] + [
            c for c in banks.doubles.members if c != code
        ]
        alerts = json.dumps([list(map(str, row)) for row in rows(database, "SELECT a.* FROM alerts a")])
        assert not [other for other in others if other.lower() in alerts.lower()], code
        for evidence in (
            "SELECT count(*) FROM audit_log WHERE action = 'LOAN_STATE_CHANGED' "
            "AND NOT (detail ? 'from' AND detail ? 'to')",
            "SELECT count(*) FROM alerts WHERE explanation_text = '' OR kinds_matched = '{}'",
            "SELECT count(*) FROM certificates WHERE as_of_seq IS NULL",
        ):
            assert scalar(database, evidence) == 0, (code, evidence)
        report = banks.agents[code].verify()
        assert report.status is IntegrityRunStatus.OK, (code, report)
        assert set(report.section_statuses().values()) == {SectionStatus.OK.value}
    for case in cases:
        holder, claimant = member_of[case["holder_member_id"]], member_of[case["claimant_member_id"]]
        for code in (holder, claimant):
            found = scalar(
                banks.databases[code],
                "SELECT count(*) FROM alerts WHERE registry_case_id = %s",
                (case["case_id"],),
            )
            assert found == 1, (code, case["case_id"])
        roles = {
            code: scalar(
                banks.databases[code],
                "SELECT role FROM alerts WHERE registry_case_id = %s",
                (case["case_id"],),
            )
            for code in (holder, claimant)
        }
        assert roles == {holder: "HOLDER", claimant: "CLAIMANT"}


class TamperBeforeFetch(Faults):
    """Changes the certificate of the first answered request at the registry before the bank fetches it."""

    def __init__(self, banks: ThreeBanks, code: str) -> None:
        super().__init__()
        self._banks = banks
        self._code = code

    def reached(self, point: FaultPoint) -> None:
        if point is FaultPoint.AFTER_RESPONSE_STORED and point not in self.passed:
            certificate_id = scalar(
                self._banks.databases[self._code],
                "SELECT certificate_id FROM registry_requests WHERE certificate_id IS NOT NULL "
                "ORDER BY responded_at DESC LIMIT 1",
            )
            self._banks.doubles.tamper(mode="CERTIFICATE_BODY", certificate_id=str(certificate_id))
        super().reached(point)


def test_each_tamper_mode_turns_the_report_broken_with_its_reason(banks: ThreeBanks) -> None:
    harbor = banks.agents["HARBOR"]
    assert harbor.verify().status is IntegrityRunStatus.OK

    database = banks.databases["HARBOR"]
    loan = scalar(database, "SELECT loan_id FROM loans WHERE state = 'LOCKED' ORDER BY loan_id LIMIT 1")
    facility = scalar(database, "SELECT facility_id FROM loans WHERE loan_id = %s", (loan,))
    assert isinstance(loan, uuid.UUID) and isinstance(facility, uuid.UUID)
    with banks.agent_with("HARBOR", TamperBeforeFetch(banks, "HARBOR")) as tampered:
        release = tampered.release(facility, [loan], date(2026, 11, 18))
        tampered.run_worker()
    outcome = "SELECT status, failure_code FROM tape_submissions WHERE submission_id = %s"
    assert rows(database, outcome, (release.submission_id,)) == [("FAILED", "CERTIFICATE_INVALID")]
    rejected = "SELECT count(*) FROM audit_log WHERE action = 'CERTIFICATE_REJECTED'"
    assert scalar(database, rejected) == 1
    report = harbor.report()
    assert report.certificates.status is SectionStatus.BROKEN
    assert [f.failure_reason for f in report.certificates.failures] == [CertificateFailure.BODY_HASH_MISMATCH]
    assert report.registry_chain.status is SectionStatus.OK

    banks.doubles.tamper(mode="BAD_CHECKPOINT_SIGNATURE")
    harbor.sync()
    report = harbor.report()
    assert report.registry_chain.status is SectionStatus.BROKEN
    assert {f.failure_reason for f in report.registry_chain.failures} == {CheckpointFailure.BAD_SIGNATURE}

    last_verified = report.registry_chain.last_verified
    assert last_verified is not None
    banks.doubles.tamper(mode="REWRITE_HISTORY", seq=last_verified.last_event_seq - 1)
    for agent in banks.agents.values():
        agent.sync()
        found = chain_reasons(agent.report())
        rewritten = {CheckpointFailure.CHAIN_BREAK, CheckpointFailure.HEAD_MISMATCH}
        assert [seq for reason, seq in found if reason in rewritten and seq is not None], (
            agent.bank_code,
            found,
        )
    submissions = banks.doubles.member_stats("HARBOR")["submissions"]
    loan = scalar(database, "SELECT loan_id FROM loans WHERE state = 'LOCKED' ORDER BY loan_id LIMIT 1")
    facility = scalar(database, "SELECT facility_id FROM loans WHERE loan_id = %s", (loan,))
    assert isinstance(loan, uuid.UUID) and isinstance(facility, uuid.UUID)
    halted = harbor.release(facility, [loan], date(2026, 11, 19))
    harbor.run_worker()
    assert rows(database, outcome, (halted.submission_id,)) == [("FAILED", "REGISTRY_INTEGRITY_BROKEN")]
    assert banks.doubles.member_stats("HARBOR")["submissions"] == submissions, "the halt rule sent nothing"
    deep = harbor.verify()
    assert any(f is CheckpointFailure.HEAD_MISMATCH and seq is not None for f, seq in chain_reasons(deep))

    banks.doubles.tamper(mode="EDIT_IN_PLACE", seq=3)
    deep = banks.agents["CALDER"].verify()
    assert (CheckpointFailure.CHAIN_BREAK, 3) in chain_reasons(deep)
    assert deep.status is IntegrityRunStatus.BROKEN
    for code, database in banks.databases.items():
        failed = scalar(database, "SELECT count(*) FROM checkpoints_seen WHERE NOT verified")
        audited = scalar(database, "SELECT count(*) FROM audit_log WHERE action = 'CHECKPOINT_REJECTED'")
        assert failed == audited and isinstance(failed, int) and failed > 0, code
        runs = scalar(database, "SELECT count(*) FROM integrity_runs")
        audited_runs = scalar(
            database, "SELECT count(*) FROM audit_log WHERE action = 'INTEGRITY_VERIFICATION_RUN'"
        )
        assert runs == audited_runs, code
