"""`SeedReferenceData`: load one bank's lenders and facilities (`onepledge-bank seed-reference`).

Runs as the owner role in one transaction and is idempotent: lenders and facilities
are upserted by id, so the same file always leaves the same contents. A facility's
status is set to ACTIVE when it is first created and is not changed afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from onepledge_bank_agent.application.ports import ReferenceSource, UnitOfWorkFactory


@dataclass(frozen=True)
class SeedResult:
    """How many lenders and facilities the file holds for the bank."""

    lenders: int
    facilities: int


class SeedReferenceData:
    """Upserts the reference data of one bank."""

    def __init__(self, units: UnitOfWorkFactory, source: ReferenceSource) -> None:
        self._units = units
        self._source = source

    def run(self, data_dir: Path, bank_code: str) -> SeedResult:
        """Read the file and upsert every lender, then every facility, in one transaction."""
        data = self._source.load(data_dir, bank_code)
        with self._units() as unit:
            for lender in data.lenders:
                unit.facilities.upsert_lender(lender)
            for facility in data.facilities:
                unit.facilities.upsert_facility(facility)
            unit.commit()
        return SeedResult(len(data.lenders), len(data.facilities))
