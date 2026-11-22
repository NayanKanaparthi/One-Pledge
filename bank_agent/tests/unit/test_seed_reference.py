"""`SeedReferenceData`: one bank's lenders and facilities upserted in one audited transaction."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from bank_testkit.memory import MemoryStore, MemoryUnits
from bank_testkit.records import lei
from onepledge_bank_agent.application.ports import ReferenceData
from onepledge_bank_agent.application.seed_reference import SeedReferenceData, UpsertCounts
from onepledge_bank_agent.domain.audit import Actor, AuditDraft
from onepledge_bank_agent.domain.entities import Facility, Lender
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, FacilityStatus
from onepledge_bank_agent.domain.errors import ReferenceDataError
from onepledge_bank_agent.domain.values import Lei

pytestmark = pytest.mark.unit

DATA_DIR = Path("reference-data")
SUMMIT = Lender(uuid.uuid4(), "Summit Auto Finance LLC", Lei(lei("00000ONEPLEDGESUM1")))
SUMMIT_LINE = Facility(
    uuid.uuid4(), SUMMIT.lender_id, "HARBOR-SUMMIT", "HARBOR-SUMMIT line", 7_500_000_000, 8500,
    FacilityStatus.ACTIVE, date(2025, 3, 1),
)  # fmt: skip


class FixedReference:
    """A `ReferenceSource` answering one bank's data, or refusing with one sentence."""

    def __init__(self, data: ReferenceData | None, sentence: str = "") -> None:
        self.data = data
        self.sentence = sentence
        self.loaded: list[tuple[Path, str]] = []

    def load(self, data_dir: Path, bank_code: str) -> ReferenceData:
        """The data, or `ReferenceDataError`."""
        self.loaded.append((data_dir, bank_code))
        if self.data is None:
            raise ReferenceDataError(self.sentence)
        return self.data


def seeded(store: MemoryStore) -> list[AuditDraft]:
    """The committed `REFERENCE_DATA_SEEDED` drafts."""
    return [d for d in store.state.audit if d.action is AuditAction.REFERENCE_DATA_SEEDED]


def test_lenders_and_facilities_are_upserted_and_a_facilitys_status_is_kept() -> None:
    store = MemoryStore()
    source = FixedReference(ReferenceData((SUMMIT,), (SUMMIT_LINE,), "a" * 64))
    seeder = SeedReferenceData(MemoryUnits(store), source, "cli")
    result = seeder.run(DATA_DIR, "HARBOR")
    assert (result.lenders, result.facilities) == (UpsertCounts(1, 0, 0), UpsertCounts(1, 0, 0))
    assert source.loaded == [(DATA_DIR, "HARBOR")]
    stored = store.state.facilities[SUMMIT_LINE.facility_id]
    assert stored == SUMMIT_LINE
    store.state.facilities[SUMMIT_LINE.facility_id] = replace(stored, status=FacilityStatus.SUSPENDED)
    again = seeder.run(DATA_DIR, "HARBOR")
    assert (again.lenders, again.facilities) == (UpsertCounts(0, 0, 1), UpsertCounts(0, 0, 1))
    assert store.state.facilities[SUMMIT_LINE.facility_id].status is FacilityStatus.SUSPENDED
    source.data = ReferenceData((SUMMIT,), (replace(SUMMIT_LINE, name="renamed line"),), "b" * 64)
    changed = seeder.run(DATA_DIR, "HARBOR")
    assert (changed.lenders, changed.facilities) == (UpsertCounts(0, 0, 1), UpsertCounts(0, 1, 0))
    assert store.state.facilities[SUMMIT_LINE.facility_id].name == "renamed line"
    assert store.state.facilities[SUMMIT_LINE.facility_id].status is FacilityStatus.SUSPENDED


def test_every_run_is_audited_with_the_file_hash_and_the_counts() -> None:
    store = MemoryStore()
    source = FixedReference(ReferenceData((SUMMIT,), (SUMMIT_LINE,), "c" * 64))
    seeder = SeedReferenceData(MemoryUnits(store), source, "cli")
    seeder.run(DATA_DIR, "HARBOR")
    seeder.run(DATA_DIR, "HARBOR")
    first, second = seeded(store)
    assert (first.actor, first.object_type, first.object_id) == (
        Actor.system(),
        AuditObjectType.REFERENCE_DATA,
        None,
    )
    assert dict(first.detail) == {
        "via": "cli", "source_sha256": "c" * 64,
        "lenders": {"inserted": 1, "updated": 0, "unchanged": 0},
        "facilities": {"inserted": 1, "updated": 0, "unchanged": 0},
    }  # fmt: skip
    assert second.detail["facilities"] == {"inserted": 0, "updated": 0, "unchanged": 1}, (
        "audited unchanged too"
    )


def test_a_refused_source_writes_nothing_and_no_entry() -> None:
    store = MemoryStore()
    seeder = SeedReferenceData(
        MemoryUnits(store), FixedReference(None, "reference.json is not valid."), "cli"
    )
    with pytest.raises(ReferenceDataError) as raised:
        seeder.run(DATA_DIR, "HARBOR")
    assert raised.value.sentence == "reference.json is not valid."
    assert (store.state.audit, store.state.lenders, store.commits) == ([], {}, 0)


def test_a_conflicting_lender_rolls_the_whole_run_back() -> None:
    store = MemoryStore()
    clash = Lender(uuid.uuid4(), "Another name LLC", SUMMIT.lei)
    other_line = replace(SUMMIT_LINE, facility_id=uuid.uuid4(), code="HARBOR-OTHER")
    seeder = SeedReferenceData(
        MemoryUnits(store), FixedReference(ReferenceData((SUMMIT,), (SUMMIT_LINE,), "d" * 64)), "cli"
    )
    seeder.run(DATA_DIR, "HARBOR")
    refused = SeedReferenceData(
        MemoryUnits(store), FixedReference(ReferenceData((clash,), (other_line,), "e" * 64)), "cli"
    )
    with pytest.raises(ReferenceDataError):
        refused.run(DATA_DIR, "HARBOR")
    assert list(store.state.lenders) == [SUMMIT.lender_id]
    assert list(store.state.facilities) == [SUMMIT_LINE.facility_id]
    assert len(seeded(store)) == 1
