"""Entities of the synthetic world: banks, lenders, facilities, users, steps, loans and outside records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class TapeKind(StrEnum):
    """Kinds of tape submission."""

    BACK_BOOK = "BACK_BOOK"
    PLEDGE = "PLEDGE"
    RELEASE = "RELEASE"


class Profile(StrEnum):
    """Size profiles of SPEC 5.6."""

    FULL = "full"
    SMALL = "small"
    EXAMPLE = "example"


@dataclass(frozen=True)
class Bank:
    """A registry member bank."""

    code: str
    name: str


@dataclass(frozen=True)
class Lender:
    """A non-bank lender with its legal name and LEI, and the spellings outside records use."""

    code: str
    legal_name: str
    lei: str
    title_spellings: tuple[str, ...]
    contract_spellings: tuple[str, ...]
    borrower_prefix: str
    borrower_digits: int


@dataclass(frozen=True)
class Facility:
    """One credit line of a bank to a lender."""

    code: str
    bank_code: str
    lender_code: str
    name: str
    credit_limit_cents: int
    advance_rate_bps: int
    opened_on: date


@dataclass(frozen=True)
class DemoUser:
    """A seeded login of a bank (no password)."""

    bank_code: str
    email: str
    display_name: str
    role: str


@dataclass(frozen=True)
class StepDefinition:
    """One tape submission of the timeline."""

    step_id: str
    scene: int | None
    bank_code: str
    facility_code: str
    kind: TapeKind
    as_of: date
    baseline_full: int
    baseline_small: int
    concurrent_group: str | None = None

    @property
    def uploader_role(self) -> str:
        """RELEASE tapes are uploaded by a credit officer, everything else by an analyst."""
        return "CREDIT_OFFICER" if self.kind == TapeKind.RELEASE else "ANALYST"

    def file_name(self) -> str:
        """`<STEP>_<FACILITY_CODE>_<KIND>.csv`."""
        return f"{self.step_id}_{self.facility_code}_{self.kind.value}.csv"

    def relative_path(self) -> str:
        """Path of the tape file inside the data directory."""
        return f"tapes/{self.bank_code}/{self.file_name()}"


@dataclass(frozen=True)
class LoanRecord:
    """One car loan as the lender records it. Copies are separate records."""

    key: int
    lender_code: str
    vin: str
    borrower_ref: str
    loan_number: str
    origination: date
    amount_cents: int
    term_months: int
    apr_tenths: int
    vehicle_year: int
    make: str
    model: str
    title_state: str


@dataclass(frozen=True)
class TitleRecord:
    """A state title office record."""

    vin: str
    state: str
    title_status: str
    lienholder_name: str | None
    lien_recorded_on: date | None


@dataclass(frozen=True)
class PaymentRecord:
    """One payment deposit."""

    payment_id: str
    vin: str
    paid_on: date
    amount_cents: int
    deposit_bank_code: str


@dataclass(frozen=True)
class ContractRecord:
    """One contract vault record."""

    contract_id: str
    vin: str
    lender_name: str
    amount_cents: int
    signed_on: date
    document_sha256: str
