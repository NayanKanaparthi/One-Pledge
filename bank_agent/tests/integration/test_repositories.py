"""SQL repositories, the audit writer and the advisory locks on a real PostgreSQL server."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from bank_testkit.audit_chain import first_broken_seq
from bank_testkit.clocks import ManualClock
from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase, insert_user, pairs
from bank_testkit.tapes import SUMMIT_LAYOUT
from onepledge_bank_agent.domain.audit import Actor, AuditDraft
from onepledge_bank_agent.domain.entities import Facility, Lender
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    AuditObjectType,
    FacilityStatus,
    FailureCode,
    Role,
    TapeKind,
    TapeSource,
    TapeStatus,
)
from onepledge_bank_agent.domain.errors import ConcurrentUploadError, ReferenceDataError
from onepledge_bank_agent.domain.tape import TapeFacts, TapeSubmission
from onepledge_bank_agent.domain.values import Lei
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


def add(engine: Engine, *tapes: TapeSubmission) -> None:
    with units(engine)() as unit:
        for item in tapes:
            unit.tapes.add(item, SUMMIT_LAYOUT)
        unit.commit()


def test_runnable_respects_upload_order_facility_order_and_backoff(
    seeded: BankDatabase, app_engine: Engine
) -> None:
    start = datetime(2026, 11, 20, 9, tzinfo=UTC)
    other_facility = second_facility(seeded)
    first, second = tape(seeded, start), tape(seeded, start + timedelta(minutes=1))
    elsewhere = tape(seeded, start + timedelta(minutes=2), facility=other_facility)
    add(app_engine, second, elsewhere, first)
    statuses = {TapeStatus.RECEIVED}
    with units(app_engine)() as unit:
        assert unit.tapes.runnable(statuses, start, 10) == [first.submission_id, elsewhere.submission_id]
        assert unit.tapes.runnable(set(), start, 10) == []
        assert unit.tapes.runnable(statuses, start, 1) == [first.submission_id]
        assert not first.record_failed_attempt(FailureCode.OUTSIDE_RECORDS_UNAVAILABLE, start, 8)
        unit.tapes.save(first)
        unit.commit()
    with units(app_engine)() as unit:
        assert unit.tapes.runnable(statuses, start, 10) == [elsewhere.submission_id], (
            "backing off and still blocking"
        )
        assert unit.tapes.runnable(statuses, start + timedelta(seconds=2), 10)[0] == first.submission_id
        stored = unit.tapes.get(first.submission_id)
        assert stored is not None and stored.progress.attempts == 1


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


def test_a_rolled_back_unit_of_work_writes_no_audit_entry(seeded: BankDatabase, app_engine: Engine) -> None:
    before = pairs(seeded, "SELECT 'n', count(*)::text FROM audit_log")
    assert before == {"n": "1"}, "only the seeding entry"
    with units(app_engine)() as unit:
        unit.record(
            AuditDraft(AuditAction.TAPE_FAILED, Actor.system(), AuditObjectType.TAPE_SUBMISSION, None)
        )
    assert pairs(seeded, "SELECT 'n', count(*)::text FROM audit_log") == before


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


def test_reference_upserts_keep_status_and_refuse_conflicting_ids(seeded: BankDatabase) -> None:
    engine = make_engine(seeded.owner_url)
    try:
        current = facility_id(seeded)
        with seeded.connect("owner") as connection:
            connection.execute(
                "UPDATE facilities SET status = 'SUSPENDED' WHERE facility_id = %s", (current,)
            )
        with units(engine)() as unit:
            found = unit.facilities.get(current)
            assert found is not None
            unit.facilities.upsert_facility(found.facility)
            unit.commit()
        assert pairs(seeded, "SELECT code, status FROM facilities WHERE code = 'HARBOR-SUMMIT'") == {
            "HARBOR-SUMMIT": "SUSPENDED"
        }
        stored_lei = pairs(
            seeded, "SELECT 'lei', lei FROM lenders WHERE lender_id = %s", (found.lender.lender_id,)
        )
        clash = Lender(uuid.uuid4(), found.lender.legal_name, Lei(stored_lei["lei"]))
        with pytest.raises(ReferenceDataError), units(engine)() as unit:
            unit.facilities.upsert_lender(clash)
        twin = Facility(
            uuid.uuid4(),
            found.lender.lender_id,
            "HARBOR-SUMMIT",
            "x",
            1,
            1,
            FacilityStatus.ACTIVE,
            date(2025, 1, 1),
        )
        with pytest.raises(ReferenceDataError), units(engine)() as unit:
            unit.facilities.upsert_facility(twin)
    finally:
        engine.dispose()
