"""The round-trip rule on PostgreSQL (SPEC_DECISIONS 100, layers 2 and 3).

- Every edge value of a stored timestamp and sequence number written through the repository
  reads back equal (Hypothesis).
- The database refuses a timestamp or a date another party supplied outside the range, whoever
  writes it.
- A superuser who drops a constraint and forces in a row the driver cannot read (the values of
  review 3 F1) leaves a report that still renders: the section is BROKEN and the evidence
  section names the table and the row as UNREADABLE; deep verification runs and stores its
  report; the command line prints the whole report and exits 1, never the last resort.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from bank_testkit.records import checkpoint
from onepledge_bank_agent.domain.enums import CheckpointFailure, TapeKind, TapeStatus
from onepledge_bank_agent.domain.integrity import EvidenceProblemKind, SectionStatus
from onepledge_bank_agent.domain.storable import BIGINT_MAX, StorableTimestamp
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.repositories.checkpoints import SqlCheckpointRepository
from onepledge_bank_agent.interface.cli.main import main
from onepledge_bank_agent.interface.cli.output import Output

pytestmark = pytest.mark.integration

TAPE = "tapes/HARBOR/S06_HARBOR-LAKESIDE_PLEDGE.csv"
FAR_FUTURE = "9999-12-31 23:59:59-12"
FAR_PAST = "0001-01-01 00:00:00+12"


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    moment=st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2099, 12, 31, 23, 59, 59, 999999)),
    seq=st.integers(min_value=1, max_value=BIGINT_MAX),
    last=st.integers(min_value=0, max_value=BIGINT_MAX),
)
def test_every_storable_timestamp_and_sequence_reads_back_equal(
    bank_db: BankDatabase, moment: datetime, seq: int, last: int
) -> None:
    engine = make_engine(bank_db.owner_url)
    stored = checkpoint(seq, failure=CheckpointFailure.BAD_SIGNATURE, signature=uuid.uuid4().hex)
    written = type(stored)(
        stored.checkpoint_id, seq, last, stored.head_hash, StorableTimestamp(moment.replace(tzinfo=UTC)),
        stored.signature, stored.signing_key_id, {"checkpoint_seq": seq, "last_event_seq": last},
        stored.received_at, stored.verified_at, stored.failure, stored.first_bad_event_seq,
    )  # fmt: skip
    try:
        with Session(engine) as session:
            SqlCheckpointRepository(session).add(written)
            session.commit()
        with Session(engine) as session:
            (read,) = [
                c for c in SqlCheckpointRepository(session).all() if c.checkpoint_id == written.checkpoint_id
            ]
    finally:
        engine.dispose()
    assert read == written


RANGE_CHECKS = {
    "ck_certificates_issued_at_range", "ck_checkpoints_seen_signed_at_range",
    "ck_tape_submissions_as_of_date_range", "ck_loans_origination_date_range",
    "ck_facilities_opened_on_range",
}  # fmt: skip


def test_every_column_another_party_fills_with_a_time_has_its_range_check(seeded: BankDatabase) -> None:
    with seeded.connect("admin") as connection:
        names = {
            row[0] for row in connection.execute("SELECT conname FROM pg_constraint WHERE contype = 'c'")
        }
        assert names >= RANGE_CHECKS
        with pytest.raises(psycopg.errors.CheckViolation) as raised:
            connection.execute("UPDATE facilities SET opened_on = DATE '2100-01-01'")
    assert raised.value.diag.constraint_name == "ck_facilities_opened_on_range"


def test_the_database_refuses_a_signed_at_outside_the_range(bank_db: BankDatabase) -> None:
    with bank_db.connect("owner") as connection, pytest.raises(psycopg.errors.CheckViolation) as raised:
        connection.execute(
            "INSERT INTO checkpoints_seen (checkpoint_id, checkpoint_seq, last_event_seq, "
            "head_hash, signed_at, "
            "signature, signing_key_id, body, received_at, verified, verified_at, failure_reason) VALUES "
            "(%s, 1, 1, %s, %s, 'c2ln', 'k-1', '{}', now(), false, now(), 'BAD_SIGNATURE')",
            (uuid.uuid4(), "a" * 64, FAR_FUTURE),
        )
    assert raised.value.diag.constraint_name == "ck_checkpoints_seen_signed_at_range"


@pytest.fixture
def seeded(bank_db: BankDatabase, small_data: Path) -> BankDatabase:
    seed_reference(bank_db, small_data)
    return bank_db


@pytest.fixture
def completed(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> Iterator[BankAgentHarness]:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(small_outside, doubles)) as harness:
        tape = harness.ingest(small_data / TAPE, "HARBOR-LAKESIDE", TapeKind.PLEDGE, date(2026, 10, 15))
        assert harness.run_until(tape, TapeStatus.COMPLETED) is TapeStatus.COMPLETED
        harness.sync()
        yield harness


def force_unreadable_rows(database: BankDatabase) -> tuple[str, str]:
    """As a superuser: drop the two range checks and copy one checkpoint and one certificate with the
    timestamps of review 3 F1. Returns the two new row ids."""
    checkpoint_id, certificate_row_id = str(uuid.uuid4()), str(uuid.uuid4())
    with database.connect("admin") as connection:
        connection.execute("ALTER TABLE checkpoints_seen DROP CONSTRAINT ck_checkpoints_seen_signed_at_range")
        connection.execute("ALTER TABLE certificates DROP CONSTRAINT ck_certificates_issued_at_range")
        connection.execute(
            "INSERT INTO checkpoints_seen (checkpoint_id, checkpoint_seq, last_event_seq, "
            "head_hash, signed_at, "
            "signature, signing_key_id, body, received_at, verified, verified_at, failure_reason) "
            "SELECT %s, checkpoint_seq + 1000, last_event_seq, head_hash, %s, signature || 'x', "
            "signing_key_id, "
            "body, received_at, false, verified_at, 'BAD_SIGNATURE' FROM checkpoints_seen LIMIT 1",
            (checkpoint_id, FAR_FUTURE),
        )
        connection.execute(
            "INSERT INTO certificates (certificate_row_id, certificate_id, request_id, "
            "registry_submission_id, "
            "kind, as_of_seq, issued_at, body, body_hash, signature, signing_key_id, verified, "
            "failure_reason, "
            "verified_at, received_at, delivered_body_sha256) SELECT %s, certificate_id, request_id, "
            "registry_submission_id, kind, as_of_seq, %s, body, body_hash, signature || 'x', signing_key_id, "
            "false, 'BAD_SIGNATURE', verified_at, received_at, delivered_body_sha256 FROM "
            "certificates LIMIT 1",
            (certificate_row_id, FAR_PAST),
        )
    return checkpoint_id, certificate_row_id


def test_rows_the_driver_cannot_read_leave_a_report_that_names_them(
    completed: BankAgentHarness, bank_db: BankDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint_id, certificate_row_id = force_unreadable_rows(bank_db)
    for report in (completed.report(), completed.verify()):
        assert report.registry_chain.status is SectionStatus.BROKEN
        assert report.certificates.status is SectionStatus.BROKEN
        assert report.audit_log.status is not SectionStatus.BROKEN
        unreadable = {
            (p.table, p.row_id)
            for p in report.evidence.problems
            if p.problem is EvidenceProblemKind.UNREADABLE
        }
        assert unreadable == {("checkpoints_seen", checkpoint_id), ("certificates", certificate_row_id)}
    for name, value in completed.environment.items():
        monkeypatch.setenv(name, value)
    for command in (["integrity", "report"], ["integrity", "verify"], ["integrity", "report", "--json"]):
        out, err = io.StringIO(), io.StringIO()
        assert main(command, Output(out, err), completed.collaborators) == 1, err.getvalue()
        assert "Unexpected error" not in err.getvalue() and out.getvalue()
    document = json.loads(out.getvalue())
    assert document["registry_chain"]["status"] == "BROKEN"
    assert {"table": "checkpoints_seen", "row_id": checkpoint_id, "problem": "UNREADABLE"} in [
        {key: problem[key] for key in ("table", "row_id", "problem")}
        for problem in document["evidence"]["problems"]
    ]
