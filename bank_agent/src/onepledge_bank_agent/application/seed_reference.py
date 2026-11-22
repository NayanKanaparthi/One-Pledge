"""`SeedReferenceData`: load one bank's lenders and facilities (`onepledge-bank seed-reference`).

Runs as the owner role in one transaction and is idempotent: lenders and facilities
are upserted by id, so the same file always leaves the same contents. A facility's
status is set to ACTIVE when it is first created and is not changed afterwards. Every
run writes one `REFERENCE_DATA_SEEDED` audit entry in the same transaction, also when
nothing changed (SPEC_DECISIONS 30).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from onepledge_bank_agent.application.ports import ReferenceSource, UnitOfWorkFactory, Upserted
from onepledge_bank_agent.domain.audit import Actor, AuditDraft
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, AuditOutcome


@dataclass(frozen=True)
class UpsertCounts:
    """How many rows of one table were inserted, updated or left unchanged."""

    inserted: int
    updated: int
    unchanged: int

    @classmethod
    def of(cls, outcomes: list[Upserted]) -> UpsertCounts:
        """Count a list of upsert outcomes."""
        found = Counter(outcomes)
        return cls(found[Upserted.INSERTED], found[Upserted.UPDATED], found[Upserted.UNCHANGED])

    @property
    def total(self) -> int:
        """Every row the file holds for this table."""
        return self.inserted + self.updated + self.unchanged

    def as_detail(self) -> dict[str, JsonValue]:
        """The audit detail form."""
        return {"inserted": self.inserted, "updated": self.updated, "unchanged": self.unchanged}


@dataclass(frozen=True)
class SeedResult:
    """What seeding did to lenders and to facilities, and the hash of the file it read."""

    lenders: UpsertCounts
    facilities: UpsertCounts
    source_sha256: str


class SeedReferenceData:
    """Upserts the reference data of one bank and audits the run."""

    def __init__(self, units: UnitOfWorkFactory, source: ReferenceSource, via: str) -> None:
        self._units = units
        self._source = source
        self._via = via

    def run(self, data_dir: Path, bank_code: str) -> SeedResult:
        """Read the file and upsert every lender, then every facility, in one audited transaction."""
        data = self._source.load(data_dir, bank_code)
        with self._units() as unit:
            lenders = UpsertCounts.of([unit.facilities.upsert_lender(lender) for lender in data.lenders])
            facilities = UpsertCounts.of(
                [unit.facilities.upsert_facility(facility) for facility in data.facilities]
            )
            result = SeedResult(lenders, facilities, data.source_sha256)
            unit.record(self._seeded(result))
            unit.commit()
        return result

    def _seeded(self, result: SeedResult) -> AuditDraft:
        return AuditDraft(
            AuditAction.REFERENCE_DATA_SEEDED,
            Actor.system(),
            AuditObjectType.REFERENCE_DATA,
            None,
            AuditOutcome.SUCCESS,
            {
                "via": self._via,
                "source_sha256": result.source_sha256,
                "lenders": result.lenders.as_detail(),
                "facilities": result.facilities.as_detail(),
            },
        )
