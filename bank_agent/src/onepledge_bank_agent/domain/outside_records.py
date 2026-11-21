"""What the three outside sources say about a vehicle, as plain records (contract `outside_records.yaml`)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TitleRecord:
    """A title office record. The lienholder is exactly as the office recorded it."""

    state: str
    title_status: str
    lienholder_name: str | None
    lien_recorded_on: date | None


@dataclass(frozen=True)
class PaymentRecord:
    """One payment received for the vehicle's loan."""

    payment_id: uuid.UUID
    paid_on: date
    amount_cents: int
    deposit_bank_code: str


@dataclass(frozen=True)
class PaymentWindow:
    """The payments returned for a date window, newest first, and the total the service reported."""

    window_from: date
    window_to: date
    payments: tuple[PaymentRecord, ...]
    total_reported: int


@dataclass(frozen=True)
class ContractRecord:
    """One contract vault record for the vehicle."""

    contract_id: uuid.UUID
    lender_name: str
    amount_cents: int
    signed_on: date


@dataclass(frozen=True)
class OutsideEvidence:
    """Everything the four local checks of one loan rest on."""

    title: TitleRecord | None
    payments: PaymentWindow
    contracts: tuple[ContractRecord, ...]
