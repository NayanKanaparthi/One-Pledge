"""The record validators agree with the contract on probes derived from it (SPEC_DECISIONS 109.2).

For each property of `TitleRecord`, `Payment` and `Contract` in `outside_records.yaml` the
probes come from the property's own schema: a string at `maxLength` and one longer, each enum
value and one outside, the values just inside and outside each `minimum`, and a hand-written
list of matching and non-matching strings for each pattern and format. Each probe replaces
one field of a valid record; the domain validator and a JSON Schema validation of the same
record must agree. A property without a validator, or a validator without a property, fails.
The bounds of decision 100 that the contract does not state (dates from 2000 to 2099, text
without control characters, amounts that fit the column) are tested on their own.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from onepledge_outside_records.domain.record_rules import RULES, record_problems

pytestmark = pytest.mark.unit

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "outside_records.yaml"
DOCUMENT: Mapping[str, Any] = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
SCHEMAS: Mapping[str, Any] = DOCUMENT["components"]["schemas"]
RECORDS = ("TitleRecord", "Payment", "Contract")
VALID: Mapping[str, dict[str, object]] = {
    "TitleRecord": {
        "vin": "1HGCM82633A004352",
        "state": "NJ",
        "title_status": "CLEAN",
        "lienholder_name": "Harbor Bank",
        "lien_recorded_on": "2026-03-01",
    },
    "Payment": {
        "payment_id": "0b7c2c1e-4f7d-4c1e-9a52-6a1f2d3c4b5a",
        "vin": "1HGCM82633A004352",
        "paid_on": "2026-03-01",
        "amount_cents": 125_000,
        "deposit_bank_code": "HARBOR",
    },
    "Contract": {
        "contract_id": "6f1e3a2b-9c8d-4e7f-8a6b-5c4d3e2f1a0b",
        "vin": "1HGCM82633A004352",
        "lender_name": "Summit Credit",
        "amount_cents": 2_500_000,
        "signed_on": "2025-12-30",
        "document_sha256": "a" * 64,
    },
}
# Hand-written strings for each pattern and format of the three records.
PATTERN_PROBES: Mapping[str, tuple[str, ...]] = {
    "^[A-HJ-NPR-Z0-9]{17}$": (
        "1HGCM82633A004352",
        "ZZZZZZZZZZZZZZZZZ",
        "1HGCM82633A00435",
        "1HGCM82633A0043521",
        "1HGCM82633A00435I",
        "1HGCM82633A00435O",
        "1HGCM82633A00435Q",
        "1hgcm82633a004352",
        "1HGCM82633A00435 ",
        "",
    ),
    "^[A-Z]{2}$": ("NJ", "ZZ", "nj", "N", "NJX", "N1", ""),
    "^[A-Z]{3,12}$": ("ABC", "ABCDEFGHIJKL", "AB", "ABCDEFGHIJKLM", "harbor", "HARBOR BANK", "HARBOR1", ""),
    "^[0-9a-f]{64}$": ("0" * 64, "f" * 64, "F" * 64, "0" * 63, "0" * 65, "g" * 64, ""),
}
FORMAT_PROBES: Mapping[str, tuple[str, ...]] = {
    "uuid": ("0b7c2c1e-4f7d-4c1e-9a52-6a1f2d3c4b5a", "not-a-uuid", "0b7c2c1e4f7d4c1e9a526a1f2d3c4b5a", ""),
    "date": ("2026-03-01", "2000-01-01", "2099-12-31", "2026-02-30", "2026-3-1", "20260301", ""),
}


def _resolved(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        resolved: Mapping[str, Any] = SCHEMAS[reference.removeprefix("#/components/schemas/")]
        return resolved
    return schema


def probes(schema: Mapping[str, Any]) -> Iterator[object]:
    """The boundary values of one property schema."""
    schema = _resolved(schema)
    types = schema.get("type")
    if "null" in (types if isinstance(types, list) else [types]):
        yield None
    if "maxLength" in schema:
        yield "B" * schema["maxLength"]
        yield "B" * (schema["maxLength"] + 1)
    yield from schema.get("enum", ())
    if "enum" in schema:
        yield "STOLEN"
    if "minimum" in schema:
        yield schema["minimum"]
        yield schema["minimum"] - 1
    if "pattern" in schema:
        yield from PATTERN_PROBES[schema["pattern"]]
    if "format" in schema:
        yield from FORMAT_PROBES[schema["format"]]


def _contract_says(record: str, document: Mapping[str, object]) -> bool:
    schema = {"$ref": f"#/components/schemas/{record}", "components": DOCUMENT["components"]}
    return Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(document)


CASES = [
    (record, field, value)
    for record in RECORDS
    for field, schema in SCHEMAS[record]["properties"].items()
    for value in probes(schema)
]


def test_every_property_has_a_validator_and_every_validator_a_property() -> None:
    for record in RECORDS:
        assert set(RULES[record]) == set(SCHEMAS[record]["properties"]), record


def test_every_pattern_and_format_has_hand_written_probes() -> None:
    for record in RECORDS:
        for schema in SCHEMAS[record]["properties"].values():
            resolved = _resolved(schema)
            assert "pattern" not in resolved or resolved["pattern"] in PATTERN_PROBES
            assert "format" not in resolved or resolved["format"] in FORMAT_PROBES


def test_the_valid_records_are_valid_for_both() -> None:
    for record in RECORDS:
        assert _contract_says(record, VALID[record])
        assert record_problems(record, VALID[record]) == []


@pytest.mark.parametrize(
    ("record", "field", "value"), CASES, ids=[f"{r}.{f}={v!r}"[:60] for r, f, v in CASES]
)
def test_the_validator_agrees_with_the_contract_on_each_probe(record: str, field: str, value: object) -> None:
    document = {**VALID[record], field: value}
    contract = _contract_says(record, document)
    problems = record_problems(record, document)
    assert contract == (problems == []), (contract, problems)
    assert all(name == field for name, _ in problems)


# SPEC_DECISIONS 100: bounds the contract does not state, held by the same validators.
BOUNDS = [
    ("TitleRecord", "lien_recorded_on", "1999-12-31", "must be a date from 2000-01-01 to 2099-12-31"),
    ("Payment", "paid_on", "2100-01-01", "must be a date from 2000-01-01 to 2099-12-31"),
    ("Contract", "signed_on", "0001-01-01", "must be a date from 2000-01-01 to 2099-12-31"),
    ("Contract", "lender_name", "Summit\x07Credit", "must not hold a control character"),
    ("TitleRecord", "lienholder_name", "Harbor\x00Bank", "must not hold a control character"),
    ("Contract", "lender_name", "Summit\ud800", "must be valid Unicode"),
    ("Payment", "amount_cents", 10**18, "must be a whole number of cents from 1 to 999,999,999,999,999,999"),
    ("Payment", "payment_id", "0B7C2C1E-4F7D-4C1E-9A52-6A1F2D3C4B5A", "must be a UUID in lower case"),
    ("Contract", "amount_cents", True, "must be a whole number of cents from 1 to 999,999,999,999,999,999"),
    # A pattern is an ECMA-262 pattern: `$` is the end of the text, not the end before a newline
    # (Python's `re.search`, which the JSON Schema library uses, would accept this one).
    ("TitleRecord", "state", "NJ\n", "must be two upper case letters"),
]


@pytest.mark.parametrize(("record", "field", "value", "problem"), BOUNDS)
def test_the_bounds_of_decision_100_are_held(record: str, field: str, value: object, problem: str) -> None:
    assert record_problems(record, {**VALID[record], field: value}) == [(field, problem)]


def test_a_sentence_never_repeats_the_value() -> None:
    for record, field, value in CASES:
        for _, problem in record_problems(record, {**VALID[record], field: value}):
            assert not (isinstance(value, str) and value and re.search(re.escape(value), problem)), problem
