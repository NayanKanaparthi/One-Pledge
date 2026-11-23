"""One validator per record type, holding every rule of `outside_records.yaml` (SPEC_DECISIONS 109).

A record is checked in the form the API returns it (`title_document` and the two others), so
what `seed` accepts is exactly what the service may answer. Besides the contract's patterns,
lengths, enums, minimums and formats, the bounds of decision 100 hold: text is valid Unicode
with no control character (U+0000 included), a date lies from 2000-01-01 to 2099-12-31, a UUID
is in its one lower case spelling (decision 75), and an amount fits the column. A problem
is a short phrase that names the rule, never the value.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from types import MappingProxyType

from onepledge_outside_records.domain.records import VIN_PATTERN, Contract, Payment, TitleRecord
from onepledge_outside_records.domain.text_values import MAX_INTEGER, parse_iso_date

DATE_MIN, DATE_MAX = "2000-01-01", "2099-12-31"
NAME_MAX_LENGTH = 200
TITLE_STATUSES = ("CLEAN", "SALVAGE", "JUNK")

type Rule = Callable[[object], str | None]


def _pattern(pattern: str, problem: str) -> Rule:
    compiled = re.compile(pattern)

    def rule(value: object) -> str | None:
        return None if isinstance(value, str) and compiled.fullmatch(value) else problem

    return rule


def _text_problem(value: str, max_length: int) -> str | None:
    if any(unicodedata.category(char) == "Cs" for char in value):
        return "must be valid Unicode"
    if any(unicodedata.category(char) == "Cc" for char in value):
        return "must not hold a control character"
    if len(value) > max_length:
        return f"must be at most {max_length} characters"
    return None


def _text(max_length: int, nullable: bool) -> Rule:
    def rule(value: object) -> str | None:
        if value is None and nullable:
            return None
        if not isinstance(value, str):
            return "must be text"
        return _text_problem(value, max_length)

    return rule


def _enum(values: tuple[str, ...]) -> Rule:
    problem = f"must be one of {', '.join(values)}"
    return lambda value: None if isinstance(value, str) and value in values else problem


def _date(nullable: bool) -> Rule:
    problem = f"must be a date from {DATE_MIN} to {DATE_MAX}"

    def rule(value: object) -> str | None:
        if value is None and nullable:
            return None
        if isinstance(value, str) and parse_iso_date(value) is not None and DATE_MIN <= value <= DATE_MAX:
            return None
        return problem

    return rule


def _cents(value: object) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_INTEGER:
        return None
    return f"must be a whole number of cents from 1 to {MAX_INTEGER:,}"


_VIN = _pattern(VIN_PATTERN.pattern, "must match the VIN pattern of the contract")
_UUID = _pattern(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "must be a UUID in lower case"
)

RULES: Mapping[str, Mapping[str, Rule]] = MappingProxyType(
    {
        "TitleRecord": MappingProxyType(
            {
                "vin": _VIN,
                "state": _pattern(r"^[A-Z]{2}$", "must be two upper case letters"),
                "title_status": _enum(TITLE_STATUSES),
                "lienholder_name": _text(NAME_MAX_LENGTH, nullable=True),
                "lien_recorded_on": _date(nullable=True),
            }
        ),
        "Payment": MappingProxyType(
            {
                "payment_id": _UUID,
                "vin": _VIN,
                "paid_on": _date(nullable=False),
                "amount_cents": _cents,
                "deposit_bank_code": _pattern(r"^[A-Z]{3,12}$", "must be 3 to 12 upper case letters"),
            }
        ),
        "Contract": MappingProxyType(
            {
                "contract_id": _UUID,
                "vin": _VIN,
                "lender_name": _text(NAME_MAX_LENGTH, nullable=False),
                "amount_cents": _cents,
                "signed_on": _date(nullable=False),
                "document_sha256": _pattern(r"^[0-9a-f]{64}$", "must be 64 lower case hexadecimal digits"),
            }
        ),
    }
)


def record_problems(record: str, document: Mapping[str, object]) -> list[tuple[str, str]]:
    """Every (field, problem) of one record in its API form, in the contract's field order."""
    return [
        (field, problem)
        for field, rule in RULES[record].items()
        if (problem := rule(document.get(field))) is not None
    ]


def title_document(record: TitleRecord) -> dict[str, object]:
    """A title record as `GET /v1/titles/{vin}` returns it."""
    return {
        "vin": record.vin,
        "state": record.state,
        "title_status": record.title_status,
        "lienholder_name": record.lienholder_name,
        "lien_recorded_on": record.lien_recorded_on.isoformat() if record.lien_recorded_on else None,
    }


def payment_document(payment: Payment) -> dict[str, object]:
    """A payment as an item of `GET /v1/payments` returns it."""
    return {
        "payment_id": payment.payment_id,
        "vin": payment.vin,
        "paid_on": payment.paid_on.isoformat(),
        "amount_cents": payment.amount_cents,
        "deposit_bank_code": payment.deposit_bank_code,
    }


def contract_document(contract: Contract) -> dict[str, object]:
    """A contract as an item of `GET /v1/contracts/{vin}` returns it."""
    return {
        "contract_id": contract.contract_id,
        "vin": contract.vin,
        "lender_name": contract.lender_name,
        "amount_cents": contract.amount_cents,
        "signed_on": contract.signed_on.isoformat(),
        "document_sha256": contract.document_sha256,
    }
