"""`SeedReferenceData` and the reference file reader."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from bank_testkit.world import World
from onepledge_bank_agent.application.seed_reference import SeedReferenceData
from onepledge_bank_agent.domain.enums import FacilityStatus
from onepledge_bank_agent.domain.errors import ReferenceDataError
from onepledge_bank_agent.infrastructure.seed_reference_file import ReferenceFileSource

pytestmark = pytest.mark.unit

LENDERS = [
    {
        "lender_id": str(uuid.uuid4()),
        "lender_code": "SUMMIT",
        "legal_name": "Summit Auto Finance LLC",
        "lei": "00000ONEPLEDGESUM134",
    },
    {
        "lender_id": str(uuid.uuid4()),
        "lender_code": "LAKESIDE",
        "legal_name": "Lakeside Motor Credit Inc",
        "lei": "00000ONEPLEDGELAK198",
    },
]


def facility(code: str, bank: str, lender: str) -> dict[str, object]:
    return {
        "facility_id": str(uuid.uuid4()), "facility_code": code, "bank_code": bank, "lender_code": lender,
        "name": f"{code} line", "credit_limit_cents": 7_500_000_000, "advance_rate_bps": 8500,
        "opened_on": "2025-03-01",
    }  # fmt: skip


def write(directory: Path, content: object) -> Path:
    (directory / "reference.json").write_text(json.dumps(content), encoding="utf-8")
    return directory


def test_one_banks_lenders_and_facilities_are_upserted_and_status_is_kept(tmp_path: Path) -> None:
    facilities = [
        facility("HARBOR-SUMMIT", "HARBOR", "SUMMIT"),
        facility("NORTHGATE-LAKESIDE", "NORTHGATE", "LAKESIDE"),
    ]
    directory = write(tmp_path, {"banks": [], "lenders": LENDERS, "facilities": facilities, "demo_users": []})
    world = World()
    seeder = SeedReferenceData(world.units, ReferenceFileSource())
    result = seeder.run(directory, "HARBOR")
    assert (result.lenders, result.facilities) == (1, 1)
    seeded_id = uuid.UUID(str(facilities[0]["facility_id"]))
    stored = world.store.state.facilities[seeded_id]
    assert stored.code == "HARBOR-SUMMIT" and stored.status is FacilityStatus.ACTIVE
    world.store.state.facilities[seeded_id] = replace(stored, status=FacilityStatus.SUSPENDED)
    assert seeder.run(directory, "HARBOR") == result
    assert world.store.state.facilities[seeded_id].status is FacilityStatus.SUSPENDED


@pytest.mark.parametrize(
    ("content", "sentence"),
    [
        (None, "reference.json was not found in the data directory."),
        ("{not json", "reference.json is not a valid reference data file."),
        ({"lenders": LENDERS, "facilities": []}, "reference.json has no facility for the bank code HARBOR."),
        (
            {"lenders": [], "facilities": [facility("HARBOR-SUMMIT", "HARBOR", "SUMMIT")]},
            "reference.json names a lender that it does not define.",
        ),
        (
            {
                "lenders": [{**LENDERS[0], "lei": "00000ONEPLEDGESUM135"}],
                "facilities": [facility("HARBOR-SUMMIT", "HARBOR", "SUMMIT")],
            },
            "reference.json has an invalid lei.",
        ),
        (
            {"lenders": LENDERS, "facilities": [facility("bad code", "HARBOR", "SUMMIT")]},
            "reference.json has an invalid facility_code.",
        ),
    ],
)
def test_an_unusable_file_is_one_sentence(tmp_path: Path, content: object, sentence: str) -> None:
    if isinstance(content, str):
        (tmp_path / "reference.json").write_text(content, encoding="utf-8")
    elif content is not None:
        write(tmp_path, content)
    with pytest.raises(ReferenceDataError) as raised:
        ReferenceFileSource().load(tmp_path, "HARBOR")
    assert raised.value.sentence == sentence
