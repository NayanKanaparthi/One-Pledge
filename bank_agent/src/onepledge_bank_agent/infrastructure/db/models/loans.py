"""`loans`, `local_checks`, `loan_tokens` (SPEC 12.1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, Index, PrimaryKeyConstraint, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import CHAR

from onepledge_bank_agent.infrastructure.db.models.base import Base, hash_column, timestamp, uuid_fk, uuid_pk


class LoanRow(Base):
    """`loans`. One live row per facility and VIN; a released car pledged again is a new row."""

    __tablename__ = "loans"
    __table_args__ = (
        Index(
            "uq_loans_facility_vin_live",
            "facility_id",
            "vin",
            unique=True,
            postgresql_where=text("state <> 'RELEASED'"),
        ),
        Index("ix_loans_facility_state", "facility_id", "state"),
        Index("ix_loans_vin", "vin"),
    )

    loan_id: Mapped[uuid.UUID] = uuid_pk()
    facility_id: Mapped[uuid.UUID] = uuid_fk("facilities.facility_id")
    first_submission_id: Mapped[uuid.UUID] = uuid_fk("tape_submissions.submission_id")
    lender_loan_number: Mapped[str] = mapped_column(Text)
    vin: Mapped[str] = mapped_column(CHAR(17))
    borrower_ref: Mapped[str] = mapped_column(Text)
    origination_date: Mapped[date] = mapped_column(Date)
    original_amount_cents: Mapped[int] = mapped_column(BigInteger)
    current_balance_cents: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = timestamp()
    updated_at: Mapped[datetime] = timestamp()


class LocalCheckRow(Base):
    """`local_checks` (insert-only)."""

    __tablename__ = "local_checks"
    __table_args__ = (
        UniqueConstraint(
            "loan_id", "submission_id", "check_type", name="uq_local_checks_loan_submission_type"
        ),
    )

    check_id: Mapped[uuid.UUID] = uuid_pk()
    loan_id: Mapped[uuid.UUID] = uuid_fk("loans.loan_id")
    submission_id: Mapped[uuid.UUID] = uuid_fk("tape_submissions.submission_id")
    check_type: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checked_at: Mapped[datetime] = timestamp()


class LoanTokenRow(Base):
    """`loan_tokens` (insert-only): this bank's private map from code to its own loan."""

    __tablename__ = "loan_tokens"
    __table_args__ = (
        PrimaryKeyConstraint("loan_id", "token_kind", "key_version", name="pk_loan_tokens"),
        Index("ix_loan_tokens_kind_token", "token_kind", "token"),
    )

    loan_id: Mapped[uuid.UUID] = uuid_fk("loans.loan_id")
    token_kind: Mapped[str] = mapped_column(Text)
    key_version: Mapped[str] = mapped_column(Text)
    token: Mapped[str] = hash_column()
    created_at: Mapped[datetime] = timestamp()
