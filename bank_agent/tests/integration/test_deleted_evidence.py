"""Evidence that has disappeared is a finding (SPEC_DECISIONS 89; review 2, quality finding 2).

A superuser switches the triggers off, deletes proof rows and switches them on again, as
the reviewer did: one certificate, all certificates, one checkpoint, the newest six, all
checkpoints, all audit rows, the newest audit rows. Each deletion turns the report BROKEN
and names the table and, where it can be known, the rows: the live report through the
expected proofs of (a), deep verification through the reconciliation of (b) and the audit
tail of (c).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from bank_testkit.banks import manifest_steps
from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from onepledge_bank_agent.domain.enums import IntegrityRunStatus
from onepledge_bank_agent.domain.integrity import (
    CertificateFinding,
    EvidenceProblem,
    EvidenceProblemKind,
    IntegrityReport,
    SectionStatus,
)

pytestmark = pytest.mark.integration

BANK = "HARBOR"
MISSING = EvidenceProblemKind.ROW_MISSING


@dataclass
class Bank:
    database: BankDatabase
    agent: BankAgentHarness

    def rows(self, query: str) -> list[tuple[object, ...]]:
        with self.database.connect("admin") as connection:
            return [tuple(row) for row in connection.execute(query)]

    def delete(self, table: str, where: str = "true") -> None:
        """Delete rows as a superuser who switches the table's triggers off first."""
        with self.database.connect("admin") as connection:
            connection.execute(f"ALTER TABLE {table} DISABLE TRIGGER ALL")
            connection.execute(f"DELETE FROM {table} WHERE {where}")  # noqa: S608 - test literals only
            connection.execute(f"ALTER TABLE {table} ENABLE TRIGGER ALL")


@pytest.fixture
def bank(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> Iterator[Bank]:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(small_outside, doubles)) as agent:
        for step in manifest_steps(small_data):
            if step.bank_code == BANK:
                agent.ingest(step.file, step.facility_code, step.kind, step.as_of, step.role)
                agent.run_worker()
        agent.sync()
        found = Bank(bank_db, agent)
        assert agent.verify().status is IntegrityRunStatus.OK, "a clean bank first"
        yield found


def problems(report: IntegrityReport, table: str) -> set[tuple[str | None, EvidenceProblemKind]]:
    return {(p.row_id, p.problem) for p in report.evidence.problems if p.table == table}


def certificate_rows(bank: Bank) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    found = bank.rows("SELECT certificate_row_id, certificate_id, request_id FROM certificates ORDER BY 1")
    return [(uuid.UUID(str(a)), uuid.UUID(str(b)), uuid.UUID(str(c))) for a, b, c in found]


def checkpoint_rows(bank: Bank) -> list[tuple[uuid.UUID, int]]:
    found = bank.rows("SELECT checkpoint_id, checkpoint_seq FROM checkpoints_seen ORDER BY checkpoint_seq")
    return [(uuid.UUID(str(a)), int(str(b))) for a, b in found]


def test_one_deleted_certificate_is_missing_live_and_missing_from_the_audit_log(bank: Bank) -> None:
    (row_id, certificate_id, _), *_ = certificate_rows(bank)
    bank.delete("certificates", f"certificate_row_id = '{row_id}'")
    live = bank.agent.report()
    assert live.status is IntegrityRunStatus.BROKEN
    assert live.certificates.status is SectionStatus.BROKEN
    assert [(f.certificate_id, f.failure_reason) for f in live.certificates.failures] == [
        (certificate_id, CertificateFinding.CERTIFICATE_MISSING)
    ]
    assert live.certificates.verified == live.certificates.total - 1
    deep = bank.agent.verify()
    assert deep.evidence.status is SectionStatus.BROKEN
    assert problems(deep, "certificates") == {(str(row_id), MISSING)}


def test_all_deleted_certificates_are_each_missing(bank: Bank) -> None:
    held = certificate_rows(bank)
    assert len(held) >= 2
    bank.delete("certificates")
    live = bank.agent.report()
    assert (live.certificates.status, live.certificates.verified) == (SectionStatus.BROKEN, 0)
    assert {f.certificate_id for f in live.certificates.failures} == {c for _, c, _ in held}
    assert {f.failure_reason for f in live.certificates.failures} == {CertificateFinding.CERTIFICATE_MISSING}
    assert problems(bank.agent.verify(), "certificates") == {(str(r), MISSING) for r, _, _ in held}


def test_one_deleted_checkpoint_is_missing_and_leaves_a_gap(bank: Bank) -> None:
    held = checkpoint_rows(bank)
    assert len(held) >= 3
    middle_id, middle_seq = held[len(held) // 2]
    bank.delete("checkpoints_seen", f"checkpoint_id = '{middle_id}'")
    deep = bank.agent.verify()
    assert deep.status is IntegrityRunStatus.BROKEN
    assert problems(deep, "checkpoints_seen") == {
        (str(middle_id), MISSING),
        (str(middle_seq), EvidenceProblemKind.SEQUENCE_GAP),
    }
    assert bank.agent.report().evidence == deep.evidence, "the live report shows the stored findings"


def test_the_newest_six_deleted_checkpoints_are_each_missing(bank: Bank) -> None:
    held = checkpoint_rows(bank)
    assert len(held) > 6
    newest = held[-6:]
    bank.delete("checkpoints_seen", f"checkpoint_seq >= {newest[0][1]}")
    deep = bank.agent.verify()
    assert deep.evidence.status is SectionStatus.BROKEN
    assert problems(deep, "checkpoints_seen") == {(str(i), MISSING) for i, _ in newest}


def test_all_deleted_checkpoints_break_the_chain_live_and_are_each_missing(bank: Bank) -> None:
    held = checkpoint_rows(bank)
    bank.delete("checkpoints_seen")
    live = bank.agent.report()
    assert live.registry_chain.status is SectionStatus.BROKEN, "answers exist, so not NOT_VERIFIED"
    assert live.status is IntegrityRunStatus.BROKEN
    deep = bank.agent.verify()
    assert deep.registry_chain.status is SectionStatus.BROKEN
    assert problems(deep, "checkpoints_seen") >= {(str(i), MISSING) for i, _ in held}


def stored_tail(bank: Bank) -> int:
    """The last seq the previous deep verification saw, as its stored report records it."""
    ((seq,),) = bank.rows(
        "SELECT (report -> 'audit_tail' ->> 'last_seq')::bigint FROM integrity_runs "
        "ORDER BY ran_at DESC LIMIT 1"
    )
    assert isinstance(seq, int)
    return seq


def test_all_deleted_audit_rows_lose_the_tail_and_every_proof_is_unaudited(bank: Bank) -> None:
    last_seq = stored_tail(bank)
    bank.delete("audit_log")
    deep = bank.agent.verify()
    assert deep.status is IntegrityRunStatus.BROKEN
    assert (deep.audit_log.status, deep.audit_log.first_bad_seq) == (SectionStatus.BROKEN, last_seq)
    unaudited = {p.table for p in deep.evidence.problems if p.problem is EvidenceProblemKind.ROW_UNAUDITED}
    assert unaudited == {"certificates", "checkpoints_seen", "integrity_runs", "tape_submissions", "loans"}


def test_the_newest_deleted_audit_rows_lose_the_tail(bank: Bank) -> None:
    last_seq = stored_tail(bank)
    run_entry = bank.rows("SELECT object_id FROM audit_log WHERE action = 'INTEGRITY_VERIFICATION_RUN'")
    bank.delete("audit_log", f"seq > {last_seq - 5}")
    deep = bank.agent.verify()
    assert (deep.audit_log.status, deep.audit_log.first_bad_seq) == (SectionStatus.BROKEN, last_seq)
    assert EvidenceProblem("integrity_runs", str(run_entry[0][0]), EvidenceProblemKind.ROW_UNAUDITED) in (
        deep.evidence.problems
    )
