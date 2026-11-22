"""`ReferenceFileSource`: the reference data file datagen writes, read from disk."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from onepledge_bank_agent.domain.enums import FacilityStatus
from onepledge_bank_agent.domain.errors import ReferenceDataError
from onepledge_bank_agent.infrastructure.seed_reference_file import ReferenceFileSource

pytestmark = pytest.mark.integration

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


def test_only_the_named_banks_facilities_and_their_lenders_are_loaded(tmp_path: Path) -> None:
    facilities = [
        facility("HARBOR-SUMMIT", "HARBOR", "SUMMIT"),
        facility("NORTHGATE-LAKESIDE", "NORTHGATE", "LAKESIDE"),
    ]
    directory = write(tmp_path, {"banks": [], "lenders": LENDERS, "facilities": facilities, "demo_users": []})
    data = ReferenceFileSource().load(directory, "HARBOR")
    assert [f.code for f in data.facilities] == ["HARBOR-SUMMIT"]
    assert data.facilities[0].status is FacilityStatus.ACTIVE
    assert [lender.legal_name for lender in data.lenders] == ["Summit Auto Finance LLC"]
    assert data.source_sha256 == hashlib.sha256((directory / "reference.json").read_bytes()).hexdigest()


def test_a_missing_file_writes_nothing_and_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ReferenceDataError):
        ReferenceFileSource().load(tmp_path, "HARBOR")


@pytest.mark.parametrize(
    ("content", "sentence"),
    [
        (None, "cannot read {dir}/reference.json: no such file."),
        ("{not json", "{dir}/reference.json is not a valid reference data file."),
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
    assert raised.value.sentence == sentence.format(dir=tmp_path)


def test_a_reference_path_that_is_a_directory_is_named_in_the_sentence(tmp_path: Path) -> None:
    (tmp_path / "reference.json").mkdir()
    with pytest.raises(ReferenceDataError) as raised:
        ReferenceFileSource().load(tmp_path, "HARBOR")
    assert raised.value.sentence == f"cannot read {tmp_path}/reference.json: is a directory."
