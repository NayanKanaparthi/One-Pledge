"""Outside record types (SPEC 6.1) and the queries over them."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from onepledge_outside_records.domain.errors import InvalidVinFormatError

VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
MIN_LIMIT, MAX_LIMIT, DEFAULT_LIMIT = 1, 200, 50


@dataclass(frozen=True)
class VinText:
    """A 17 character VIN in the contract's format. The check digit is not verified (SPEC 6.2)."""

    value: str

    @classmethod
    def parse(cls, text: str) -> VinText:
        """Validate the pattern, or raise InvalidVinFormatError (which never carries the text)."""
        if not VIN_PATTERN.fullmatch(text):
            raise InvalidVinFormatError
        return cls(text)

    def __repr__(self) -> str:
        """Never show the VIN."""
        return "VinText(***)"


@dataclass(frozen=True)
class TitleRecord:
    """A state title office record."""

    vin: str
    state: str
    title_status: str
    lienholder_name: str | None
    lien_recorded_on: date | None


@dataclass(frozen=True)
class Payment:
    """One payment deposit."""

    payment_id: str
    vin: str
    paid_on: date
    amount_cents: int
    deposit_bank_code: str


@dataclass(frozen=True)
class Contract:
    """One contract vault record."""

    contract_id: str
    vin: str
    lender_name: str
    amount_cents: int
    signed_on: date
    document_sha256: str


@dataclass(frozen=True)
class ApiClient:
    """An authenticated caller of the service."""

    client_id: str
    name: str


@dataclass(frozen=True)
class PaymentQuery:
    """`GET /v1/payments` parameters after validation."""

    vin: VinText
    paid_from: date | None
    paid_to: date | None
    limit: int = DEFAULT_LIMIT
    offset: int = 0


@dataclass(frozen=True)
class PaymentPage:
    """A page of payments, newest first, and the total matching count."""

    items: tuple[Payment, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class RecordCounts:
    """Rows per table after seeding."""

    titles: int
    payments: int
    contracts: int
