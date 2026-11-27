"""`ReferenceFileSource`: the reference data file datagen writes, read from disk."""

from __future__ import annotations

import errno
import hashlib
import json
import os
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


HARBOR_SUMMIT = facility("HARBOR-SUMMIT", "HARBOR", "SUMMIT")


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
        (None, "{dir}/reference.json: no such file."),
        ("{not json", "{dir}/reference.json: not valid JSON."),
        ('{"lenders": [], "lenders": [], "facilities": []}', "{dir}/reference.json: not valid JSON."),
        ('{"lenders": [], "facilities": [], "x": NaN}', "{dir}/reference.json: not valid JSON."),
        (
            '{"lenders": [], "facilities": [], "x": ' + "[" * 33 + "]" * 33 + "}",
            "{dir}/reference.json: nests too deeply.",
        ),
        (
            '{"lenders": [], "facilities": [], "x": ' + "[" * 100_000 + "]" * 100_000 + "}",
            "{dir}/reference.json: nests too deeply.",
        ),
        ("\udcff\udcfe{}", "{dir}/reference.json: not valid JSON."),  # bytes ff fe: not UTF-8
        ('{"lenders": {}, "facilities": []}', "{dir}/reference.json: not a reference data file."),
        (" " * (5 * 1024 * 1024 + 1), "{dir}/reference.json: larger than 5 MiB."),
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
        (
            {"lenders": [{**LENDERS[0], "legal_name": "Summit\x00"}], "facilities": [HARBOR_SUMMIT]},
            "{dir}/reference.json: holds a value that cannot be stored.",
        ),
        (
            {"lenders": [{**LENDERS[0], "legal_name": "Summit\ud800"}], "facilities": [HARBOR_SUMMIT]},
            "{dir}/reference.json: holds a value that cannot be stored.",
        ),
        (
            {"lenders": LENDERS, "facilities": [{**HARBOR_SUMMIT, "credit_limit_cents": 7_500_000_000.0}]},
            "{dir}/reference.json: not a reference data file.",
        ),
        (
            {"lenders": LENDERS, "facilities": [{**HARBOR_SUMMIT, "advance_rate_bps": "8500"}]},
            "{dir}/reference.json: not a reference data file.",
        ),
        (
            {
                "lenders": [{**LENDERS[0], "lender_id": LENDERS[0]["lender_id"].upper()}],
                "facilities": [HARBOR_SUMMIT],
            },
            "{dir}/reference.json: not a reference data file.",
        ),
        (
            {"lenders": LENDERS, "facilities": [{**HARBOR_SUMMIT, "credit_limit_cents": 2**63}]},
            "reference.json has an invalid credit_limit_cents.",
        ),
    ],
)
def test_an_unusable_file_is_one_sentence(tmp_path: Path, content: object, sentence: str) -> None:
    """SPEC_DECISIONS 74: a file the command did not write is hostile input, bounded and named."""
    if isinstance(content, str):
        (tmp_path / "reference.json").write_bytes(content.encode("utf-8", "surrogateescape"))
    elif content is not None:
        write(tmp_path, content)
    with pytest.raises(ReferenceDataError) as raised:
        ReferenceFileSource().load(tmp_path, "HARBOR")
    assert raised.value.sentence == sentence.format(dir=tmp_path)


def test_a_reference_path_that_is_a_directory_is_named_in_the_sentence(tmp_path: Path) -> None:
    (tmp_path / "reference.json").mkdir()
    with pytest.raises(ReferenceDataError) as raised:
        ReferenceFileSource().load(tmp_path, "HARBOR")
    # The operating system's own words for the error, as the product prints them (not one platform's).
    assert raised.value.sentence == f"{tmp_path}/reference.json: {os.strerror(errno.EISDIR).lower()}."


def test_a_number_literal_longer_than_30_characters_is_not_valid_json(tmp_path: Path) -> None:
    """Decision 74: the bounded parser of decision 38 refuses it before any value is built."""
    text = json.dumps({"lenders": LENDERS, "facilities": [HARBOR_SUMMIT]}).replace("7500000000", "1" * 31, 1)
    (tmp_path / "reference.json").write_text(text, encoding="utf-8")
    with pytest.raises(ReferenceDataError) as raised:
        ReferenceFileSource().load(tmp_path, "HARBOR")
    assert raised.value.sentence == f"{tmp_path}/reference.json: not valid JSON."
