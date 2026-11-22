"""What only PostgreSQL can show: concurrent writers, the gapless audit chain under them, advisory locks.

The behaviour of each repository port is defined once, in `tests/ports/`, and run against
both implementations; the tests here are the concurrency properties a single thread cannot reach.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from bank_testkit.audit_chain import first_broken_seq
from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase, insert_user, pairs
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.memory.clocks import ManualClock
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from bank_testkit.tapes import SUMMIT_LAYOUT, summit_row, summit_tape
from onepledge_bank_agent.domain.audit import Actor, AuditDraft
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    AuditObjectType,
    Role,
    TapeKind,
    TapeSource,
)
from onepledge_bank_agent.domain.errors import ConcurrentUploadError, TapeDuplicateError
from onepledge_bank_agent.domain.tape import TapeFacts, TapeSubmission
from onepledge_bank_agent.infrastructure.audit import HashChainedAuditWriter
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.locks import SqlSubmissionLocks
from onepledge_bank_agent.infrastructure.db.session import SqlUnitOfWorkFactory
from onepledge_bank_agent.infrastructure.ids import RandomIds

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded(bank_db: BankDatabase, example_data: Path) -> BankDatabase:
    seed_reference(bank_db, example_data)
    return bank_db


@pytest.fixture
def app_engine(seeded: BankDatabase) -> Iterator[Engine]:
    engine = make_engine(seeded.app_url)
    yield engine
    engine.dispose()


def units(engine: Engine, clock: ManualClock | None = None) -> SqlUnitOfWorkFactory:
    stamp = clock or ManualClock()
    return SqlUnitOfWorkFactory(engine, HashChainedAuditWriter(stamp, RandomIds()), RandomIds())


def facility_id(database: BankDatabase) -> uuid.UUID:
    found = pairs(database, "SELECT code, facility_id::text FROM facilities")
    return uuid.UUID(found["HARBOR-SUMMIT"])


def second_facility(database: BankDatabase) -> uuid.UUID:
    """A further facility of the seeded lender, written through the repository as the owner role."""
    engine = make_engine(database.owner_url)
    try:
        with units(engine)() as unit:
            found = unit.facilities.get(facility_id(database))
            assert found is not None
            other = replace(found.facility, facility_id=uuid.uuid4(), code="HARBOR-SECOND")
            unit.facilities.upsert_facility(other)
            unit.commit()
    finally:
        engine.dispose()
    return other.facility_id


def tape(
    database: BankDatabase, uploaded: datetime, sha: str | None = None, facility: uuid.UUID | None = None
) -> TapeSubmission:
    user = insert_user(database, Role.ANALYST)
    sha = sha or uuid.uuid4().hex * 2
    facts = TapeFacts(
        uuid.uuid4(), facility or facility_id(database), TapeKind.PLEDGE, TapeSource.FILE_UPLOAD, "t.csv",
        sha, "b" * 64, date(2026, 10, 15), 1, user, uploaded,
    )  # fmt: skip
    return TapeSubmission.receive(facts)


def test_a_concurrent_insert_of_the_same_file_raises_concurrent_upload(
    seeded: BankDatabase, app_engine: Engine
) -> None:
    sha = "d" * 64
    first, second = tape(seeded, datetime.now(UTC), sha), tape(seeded, datetime.now(UTC), sha)
    holder = units(app_engine)()
    with holder as unit:
        unit.tapes.add(first, SUMMIT_LAYOUT)
        outcome: list[BaseException | None] = []

        def compete() -> None:
            try:
                with units(app_engine)() as other:
                    other.tapes.add(second, SUMMIT_LAYOUT)
                    other.commit()
                outcome.append(None)
            except BaseException as error:  # the waiting insert fails once the first commits
                outcome.append(error)

        thread = threading.Thread(target=compete)
        thread.start()
        thread.join(0.5)
        unit.commit()
    thread.join()
    assert len(outcome) == 1 and isinstance(outcome[0], ConcurrentUploadError)
    with units(app_engine)() as unit:
        assert unit.tapes.find_by_file(first.facts.facility_id, "PLEDGE", sha) == first.submission_id


def test_the_duplicate_lookup_keys_on_facility_kind_and_file_together(
    seeded: BankDatabase, example_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> None:
    """SPEC_DECISIONS 53.2: the same bytes under another kind or another facility are new submissions."""
    other_facility = second_facility(seeded)
    data = summit_tape([summit_row()])
    as_of = date(2026, 10, 15)
    with BankAgentHarness(seeded, Services(example_outside, doubles)) as harness:
        first = harness.ingest(data, "HARBOR-SUMMIT", TapeKind.PLEDGE, as_of)
        other_kind = harness.ingest(data, "HARBOR-SUMMIT", TapeKind.BACK_BOOK, as_of)
        other_place = harness.ingest(data, "HARBOR-SECOND", TapeKind.PLEDGE, as_of)
        with pytest.raises(TapeDuplicateError) as raised:
            harness.ingest(data, "HARBOR-SUMMIT", TapeKind.PLEDGE, date(2026, 10, 1))
        assert raised.value.existing_submission_id == first.submission_id
        with pytest.raises(TapeDuplicateError) as again:
            harness.ingest(data, "HARBOR-SECOND", TapeKind.PLEDGE, as_of)
        assert again.value.existing_submission_id == other_place.submission_id
    assert len({first.submission_id, other_kind.submission_id, other_place.submission_id}) == 3
    assert other_place.facts.facility_id == other_facility
    stored = "SELECT submission_id::text, kind || ' ' || facility_id::text FROM tape_submissions"
    assert pairs(seeded, stored) == {
        str(first.submission_id): f"PLEDGE {first.facts.facility_id}",
        str(other_kind.submission_id): f"BACK_BOOK {first.facts.facility_id}",
        str(other_place.submission_id): f"PLEDGE {other_facility}",
    }


def test_concurrent_audit_appends_keep_the_chain_gapless(seeded: BankDatabase, app_engine: Engine) -> None:
    factory = units(app_engine)
    actor = Actor.system()

    def append(batch: int) -> None:
        for _ in range(5):
            with factory() as unit:
                for _ in range(batch):
                    unit.record(
                        AuditDraft(
                            AuditAction.TAPE_STAGE_ADVANCED,
                            actor,
                            AuditObjectType.TAPE_SUBMISSION,
                            uuid.uuid4(),
                            detail={"count": batch},
                        )
                    )
                unit.commit()

    threads = [threading.Thread(target=append, args=(size,)) for size in (1, 2, 3, 4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    seeding = 1  # the REFERENCE_DATA_SEEDED entry of the fixture
    assert pairs(seeded, "SELECT 'n', count(*)::text FROM audit_log") == {
        "n": str(seeding + 5 * (1 + 2 + 3 + 4))
    }
    assert first_broken_seq(seeded) is None


def test_an_advisory_lock_is_exclusive_until_released(seeded: BankDatabase, app_engine: Engine) -> None:
    second_engine = make_engine(seeded.app_url)
    locks, other = SqlSubmissionLocks(app_engine), SqlSubmissionLocks(second_engine)
    submission = uuid.uuid4()
    try:
        with locks.hold(submission) as granted:
            assert granted
            with other.hold(submission) as second:
                assert not second
            with other.hold(uuid.uuid4()) as unrelated:
                assert unrelated
        with other.hold(submission) as after:
            assert after
    finally:
        second_engine.dispose()
