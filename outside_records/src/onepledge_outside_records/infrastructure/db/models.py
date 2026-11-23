"""SQLAlchemy models of the outside records database (SPEC 6.1). They must equal the migrations."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date, datetime
from types import MappingProxyType

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import CHAR

_VIN = "~ '^[A-HJ-NPR-Z0-9]{17}$'"
_NO_CONTROL = r"!~ '[\x01-\x1f\x7f-\x9f]'"
_DAYS = "BETWEEN DATE '2000-01-01' AND DATE '2099-12-31'"
# The rules of `outside_records.yaml` and decision 100 that 0001 did not hold (SPEC_DECISIONS 109.5),
# added by migration 0003; table to constraint name to condition.
CONTRACT_CHECKS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "title_records": MappingProxyType(
            {
                "ck_title_records_state": "state ~ '^[A-Z]{2}$'",
                "ck_title_records_lienholder_name": (
                    f"lienholder_name IS NULL OR (char_length(lienholder_name) <= 200 "
                    f"AND lienholder_name {_NO_CONTROL})"
                ),
                "ck_title_records_lien_recorded_on": f"lien_recorded_on IS NULL OR lien_recorded_on {_DAYS}",
            }
        ),
        "payments": MappingProxyType(
            {
                "ck_payments_vin": f"vin {_VIN}",
                "ck_payments_paid_on": f"paid_on {_DAYS}",
                "ck_payments_deposit_bank_code": "deposit_bank_code ~ '^[A-Z]{3,12}$'",
            }
        ),
        "contracts": MappingProxyType(
            {
                "ck_contracts_vin": f"vin {_VIN}",
                "ck_contracts_lender_name": f"char_length(lender_name) <= 200 AND lender_name {_NO_CONTROL}",
                "ck_contracts_signed_on": f"signed_on {_DAYS}",
            }
        ),
    }
)


def _checks(table: str) -> tuple[CheckConstraint, ...]:
    return tuple(CheckConstraint(condition, name=name) for name, condition in CONTRACT_CHECKS[table].items())


class Base(DeclarativeBase):
    """Declarative base of the four tables."""


class TitleRecordRow(Base):
    """`title_records`."""

    __tablename__ = "title_records"
    __table_args__ = (
        CheckConstraint("vin ~ '^[A-HJ-NPR-Z0-9]{17}$'", name="ck_title_records_vin"),
        CheckConstraint("title_status IN ('CLEAN', 'SALVAGE', 'JUNK')", name="ck_title_records_title_status"),
        *_checks("title_records"),
    )

    vin: Mapped[str] = mapped_column(CHAR(17), primary_key=True)
    state: Mapped[str] = mapped_column(CHAR(2))
    title_status: Mapped[str] = mapped_column(Text)
    lienholder_name: Mapped[str | None] = mapped_column(Text)
    lien_recorded_on: Mapped[date | None] = mapped_column(Date)


class PaymentRow(Base):
    """`payments`."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_payments_amount_cents"),
        *_checks("payments"),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    vin: Mapped[str] = mapped_column(CHAR(17))
    paid_on: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    deposit_bank_code: Mapped[str] = mapped_column(Text)


class ContractRow(Base):
    """`contracts` (several rows per VIN are allowed: a refinance has two contracts)."""

    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_contracts_amount_cents"),
        CheckConstraint("document_sha256 ~ '^[0-9a-f]{64}$'", name="ck_contracts_document_sha256"),
        *_checks("contracts"),
        Index("ix_contracts_vin_signed_on", "vin", "signed_on", "contract_id"),
    )

    contract_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    vin: Mapped[str] = mapped_column(CHAR(17))
    lender_name: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    signed_on: Mapped[date] = mapped_column(Date)
    document_sha256: Mapped[str] = mapped_column(CHAR(64))


class ApiClientRow(Base):
    """`api_clients`: callers, stored by the SHA-256 of their key."""

    __tablename__ = "api_clients"
    __table_args__ = (
        CheckConstraint("api_key_sha256 ~ '^[0-9a-f]{64}$'", name="ck_api_clients_api_key_sha256"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    api_key_sha256: Mapped[str] = mapped_column(CHAR(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_payments_vin_paid_on", PaymentRow.vin, PaymentRow.paid_on.desc(), PaymentRow.payment_id)
