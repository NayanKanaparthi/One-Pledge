"""`lenders`, `facilities`, `users`, `login_attempts` (SPEC 12.1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, Identity, Index, Integer, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import CHAR

from onepledge_bank_agent.infrastructure.db.models.base import (
    C_TEXT,
    Base,
    hash_column,
    timestamp,
    uuid_fk,
    uuid_pk,
)


class LenderRow(Base):
    """`lenders`."""

    __tablename__ = "lenders"

    lender_id: Mapped[uuid.UUID] = uuid_pk()
    legal_name: Mapped[str] = mapped_column(C_TEXT, unique=True)
    lei: Mapped[str] = mapped_column(CHAR(20, collation="C"), unique=True)
    created_at: Mapped[datetime] = timestamp()


class FacilityRow(Base):
    """`facilities`."""

    __tablename__ = "facilities"

    facility_id: Mapped[uuid.UUID] = uuid_pk()
    lender_id: Mapped[uuid.UUID] = uuid_fk("lenders.lender_id")
    code: Mapped[str] = mapped_column(C_TEXT, unique=True)
    name: Mapped[str] = mapped_column(C_TEXT)
    credit_limit_cents: Mapped[int] = mapped_column(BigInteger)
    advance_rate_bps: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(C_TEXT)
    opened_on: Mapped[date] = mapped_column(Date)


class UserRow(Base):
    """`users`."""

    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(C_TEXT, unique=True)
    display_name: Mapped[str] = mapped_column(C_TEXT)
    role: Mapped[str] = mapped_column(C_TEXT)
    password_hash: Mapped[str] = mapped_column(C_TEXT)
    active: Mapped[bool] = mapped_column(Boolean)
    token_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = timestamp()
    updated_at: Mapped[datetime] = timestamp()


class LoginAttemptRow(Base):
    """`login_attempts`."""

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_email_sha256_at", "email_sha256", "at"),
        Index("ix_login_attempts_client_addr_at", "client_addr", "at"),
    )

    attempt_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    email_sha256: Mapped[str] = hash_column()
    client_addr: Mapped[str] = mapped_column(INET)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = timestamp()
