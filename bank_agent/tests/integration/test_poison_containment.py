"""Review 2, F1 on PostgreSQL and the real registry double: a registry message the bank cannot store.

The two CHECKPOINT messages of the review (a `checkpoint_seq` of 2^63, a 31 digit
`last_event_seq`) are valid against the schema but do not fit a `bigint`. Before decision 85
each made every sync fail with INTERNAL_ERROR, was never acknowledged and starved the worker.
Now each is quarantined as VALUE_OUT_OF_RANGE, audited against its row, acknowledged, the
next message is applied, the halt rule holds and a tape uploaded afterwards still runs up to
the submit stage.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase, pairs
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from onepledge_bank_agent.domain.enums import TapeKind, TapeStatus
from onepledge_bank_agent.domain.integrity import SectionStatus

pytestmark = pytest.mark.integration

TAPE = "tapes/HARBOR/S06_HARBOR-LAKESIDE_PLEDGE.csv"
SECOND = "tapes/HARBOR/S07_HARBOR-SUMMIT_PLEDGE.csv"
AS_OF = date(2026, 10, 15)
SIGNATURE = "A" * 86 + "=="


@pytest.fixture
def agent(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> Iterator[BankAgentHarness]:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(small_outside, doubles)) as harness:
        yield harness


def body(**numbers: int) -> dict[str, object]:
    return {
        "schema_version": 1, "checkpoint_seq": 1, "last_event_seq": 9000, "head_hash": "a" * 64,
        "signed_at": "2026-11-22T00:00:00Z", **numbers,
    }  # fmt: skip


def test_checkpoints_with_numbers_too_large_to_store_are_quarantined_and_nothing_starves(
    agent: BankAgentHarness, bank_db: BankDatabase, small_data: Path, doubles: InProcessDoubles
) -> None:
    first = agent.ingest(small_data / TAPE, "HARBOR-LAKESIDE", TapeKind.PLEDGE, AS_OF)
    assert agent.run_until(first, TapeStatus.COMPLETED) is TapeStatus.COMPLETED
    seen_before = pairs(bank_db, "SELECT 'n', count(*)::text FROM checkpoints_seen")["n"]
    for numbers in ({"checkpoint_seq": 2**63}, {"last_event_seq": 10**30}):
        payload = {"body": body(**numbers), "signature": SIGNATURE, "signing_key_id": "registry-local-1"}
        doubles.inject_message("HARBOR", "CHECKPOINT", payload)
    agent.sync()
    quarantined = pairs(
        bank_db, "SELECT payload->'body'->>'checkpoint_seq', reason FROM registry_messages_quarantined"
    )
    assert quarantined == {str(2**63): "VALUE_OUT_OF_RANGE", "1": "VALUE_OUT_OF_RANGE"}
    audited = pairs(
        bank_db,
        "SELECT q.quarantine_id::text, a.detail->>'reason' FROM registry_messages_quarantined q "
        "JOIN audit_log a ON a.object_id = q.quarantine_id AND a.action = 'REGISTRY_MESSAGE_QUARANTINED'",
    )
    assert sorted(audited.values()) == ["VALUE_OUT_OF_RANGE", "VALUE_OUT_OF_RANGE"]
    assert doubles.forged_pending("HARBOR") == 0, "both acknowledged"
    assert pairs(bank_db, "SELECT 'n', count(*)::text FROM checkpoints_seen")["n"] == seen_before
    second = agent.ingest(small_data / SECOND, "HARBOR-SUMMIT", TapeKind.PLEDGE, AS_OF)
    report = agent.run_worker()
    assert report.task_failures == []
    status = pairs(
        bank_db,
        "SELECT 'x', status || ' ' || coalesce(failure_code, '') FROM tape_submissions "
        "WHERE submission_id = %s",
        (second.submission_id,),
    )
    assert status == {"x": "FAILED REGISTRY_INTEGRITY_BROKEN"}, "it ran up to submit; the halt holds"
    integrity = agent.report()
    assert integrity.registry_messages.status is SectionStatus.BROKEN
    assert integrity.registry_messages.quarantined == 2
