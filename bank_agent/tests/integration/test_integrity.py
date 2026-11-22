"""Deep verification and the live integrity report (SPEC 15.5, 15.6) against edits by a superuser.

The superuser switches the immutability triggers off, edits a stored row and switches
them on again, as a privileged insider could. Deep verification recomputes every hash
from stored data and must find and locate each edit; the live report must show a
control that is switched off.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase
from bank_testkit.doubles import REGISTRY_URL, InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from onepledge_bank_agent.domain.dependency_errors import RegistryUnavailableError
from onepledge_bank_agent.domain.enums import IntegrityRunStatus, TapeKind
from onepledge_bank_agent.domain.integrity import DatabaseControl, SectionStatus
from onepledge_bank_agent.infrastructure.db.migrations import head_revision

pytestmark = pytest.mark.integration

TAPE = "tapes/HARBOR/S06_HARBOR-LAKESIDE_PLEDGE.csv"


@dataclass
class Bank:
    database: BankDatabase
    agent: BankAgentHarness
    doubles: InProcessDoubles

    def run(self, *statements: str) -> object:
        with self.database.connect("admin") as connection:
            found: object = None
            for statement in statements:
                cursor = connection.execute(statement)
                if cursor.description is not None:
                    row = cursor.fetchone()
                    found = row[0] if row is not None else None
            return found


@pytest.fixture
def bank(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> Iterator[Bank]:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(small_outside, doubles)) as agent:
        tape = agent.ingest(small_data / TAPE, "HARBOR-LAKESIDE", TapeKind.PLEDGE, date(2026, 10, 15))
        agent.run_worker()
        locked = Bank(bank_db, agent, doubles).run(
            "SELECT loan_id FROM loans WHERE state = 'LOCKED' ORDER BY loan_id LIMIT 1"
        )
        assert isinstance(locked, uuid.UUID)
        agent.release(tape.facts.facility_id, [locked], date(2026, 10, 20))
        agent.run_worker()
        yield Bank(bank_db, agent, doubles)


def test_before_the_first_deep_verification_the_report_says_what_it_has_not_verified(bank: Bank) -> None:
    report = bank.agent.report()
    assert report.last_deep_verification_at is None
    assert report.audit_log.status is SectionStatus.NOT_VERIFIED
    assert report.raw_data.status is SectionStatus.NOT_VERIFIED
    assert report.registry_chain.status is SectionStatus.OK
    assert report.certificates.status is SectionStatus.OK and report.certificates.total >= 2
    assert report.database_controls.status is SectionStatus.OK
    assert [name for name, _ in report.database_controls.controls] == list(DatabaseControl)
    assert report.status is IntegrityRunStatus.OK


def test_an_edited_audit_entry_and_an_edited_raw_row_are_found_and_located(bank: Bank) -> None:
    first = bank.agent.verify()
    assert first.status is IntegrityRunStatus.OK
    assert first.raw_data.submissions_checked == 2 and first.raw_data.rows_checked > 1
    bank.run(
        "ALTER TABLE audit_log DISABLE TRIGGER ALL",
        "UPDATE audit_log SET detail = detail || '{\"x\": 1}' WHERE seq = 7",
        "ALTER TABLE audit_log ENABLE TRIGGER ALL",
    )
    audit = bank.agent.verify()
    assert audit.status is IntegrityRunStatus.BROKEN
    assert (audit.audit_log.status, audit.audit_log.first_bad_seq) == (SectionStatus.BROKEN, 7)
    edited = bank.run(
        "ALTER TABLE loans_raw DISABLE TRIGGER ALL",
        'UPDATE loans_raw SET raw_payload = raw_payload || \'{"x": "1"}\' '
        "WHERE raw_id = (SELECT raw_id FROM loans_raw ORDER BY raw_id LIMIT 1) RETURNING submission_id",
        "ALTER TABLE loans_raw ENABLE TRIGGER ALL",
    )
    raw = bank.agent.verify()
    assert raw.raw_data.status is SectionStatus.BROKEN and raw.raw_data.bad_submission_ids == (edited,)
    live = bank.agent.report()
    assert live.audit_log.first_bad_seq == 7 and live.raw_data.bad_submission_ids == (edited,)
    runs = bank.run("SELECT count(*) FROM integrity_runs")
    audited = bank.run("SELECT count(*) FROM audit_log WHERE action = 'INTEGRITY_VERIFICATION_RUN'")
    assert runs == audited == 3


def test_a_release_instruction_whose_stored_row_changed_is_found(bank: Bank) -> None:
    edited = bank.run(
        "ALTER TABLE loans_raw DISABLE TRIGGER ALL",
        "UPDATE loans_raw r SET raw_text = n.id, raw_cells = jsonb_build_array(n.id), "
        "raw_payload = jsonb_build_object('loan_id', n.id), "
        "row_sha256 = encode(sha256(convert_to(n.id, 'UTF8')), 'hex') "
        "FROM tape_submissions t, (SELECT gen_random_uuid()::text AS id) n "
        "WHERE t.submission_id = r.submission_id AND t.source = 'API_INSTRUCTION' RETURNING r.submission_id",
        "ALTER TABLE loans_raw ENABLE TRIGGER ALL",
    )
    report = bank.agent.verify()
    assert report.raw_data.bad_submission_ids == (edited,)


def test_a_control_switched_off_is_shown_live_and_back_on_when_restored(bank: Bank) -> None:
    bank.run("ALTER TABLE loans_raw DISABLE TRIGGER USER")
    try:
        report = bank.agent.report()
    finally:
        bank.run("ALTER TABLE loans_raw ENABLE TRIGGER USER")
    controls = dict(report.database_controls.controls)
    assert report.database_controls.status is SectionStatus.BROKEN
    assert controls[DatabaseControl.LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED] is False
    assert [name for name, ok in controls.items() if not ok] == [
        DatabaseControl.LOANS_RAW_IMMUTABILITY_TRIGGER_ENABLED
    ]
    assert bank.agent.report().database_controls.status is SectionStatus.OK
    bank.run("UPDATE alembic_version SET version_num = '0003_grants'")
    behind = dict(bank.agent.report().database_controls.controls)
    with bank.database.connect("admin") as connection:
        connection.execute("UPDATE alembic_version SET version_num = %s", (head_revision(),))
    assert behind[DatabaseControl.SCHEMA_AT_MIGRATION_HEAD] is False


def test_deep_verification_needs_the_registry_and_stores_nothing_without_it(bank: Bank) -> None:
    bank.doubles.down.add(REGISTRY_URL)
    with pytest.raises(RegistryUnavailableError):
        bank.agent.verify()
    bank.doubles.down.clear()
    assert bank.run("SELECT count(*) FROM integrity_runs") == 0
    assert bank.agent.verify().status is IntegrityRunStatus.OK
