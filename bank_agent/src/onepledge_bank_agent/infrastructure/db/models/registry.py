"""`registry_requests`, `registry_request_items`, `certificates`, `checkpoints_seen`, `alerts` (SPEC 12.1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import CHAR

from onepledge_bank_agent.infrastructure.db.models.base import (
    Base,
    hash_column,
    timestamp,
    uuid_column,
    uuid_fk,
    uuid_pk,
)


class RegistryRequestRow(Base):
    """`registry_requests`: each planned request with its stored response."""

    __tablename__ = "registry_requests"
    __table_args__ = (
        Index(
            "uq_registry_requests_tape_batch",
            "submission_id",
            "batch_index",
            unique=True,
            postgresql_where=text("origin = 'TAPE'"),
        ),
    )

    request_id: Mapped[uuid.UUID] = uuid_pk()
    submission_id: Mapped[uuid.UUID] = uuid_fk("tape_submissions.submission_id")
    origin: Mapped[str] = mapped_column(Text)
    batch_index: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(CHAR(64), unique=True)
    kind: Mapped[str] = mapped_column(Text)
    key_version: Mapped[str] = mapped_column(Text)
    item_count: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    registry_submission_id: Mapped[uuid.UUID | None] = uuid_column(nullable=True)
    as_of_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    certificate_id: Mapped[uuid.UUID | None] = uuid_column(nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class RegistryRequestItemRow(Base):
    """`registry_request_items`: one item of a request and the registry's result for it."""

    __tablename__ = "registry_request_items"
    __table_args__ = (
        PrimaryKeyConstraint("request_id", "item_ref", name="pk_registry_request_items"),
        UniqueConstraint("request_id", "loan_id", name="uq_registry_request_items_request_loan"),
        UniqueConstraint("request_id", "position", name="uq_registry_request_items_request_position"),
        Index("ix_registry_request_items_loan_id", "loan_id"),
    )

    request_id: Mapped[uuid.UUID] = uuid_fk("registry_requests.request_id")
    item_ref: Mapped[uuid.UUID] = uuid_column()
    position: Mapped[int] = mapped_column(Integer)
    loan_id: Mapped[uuid.UUID] = uuid_fk("loans.loan_id")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    already_held: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    match_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    kinds_matched: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    registry_case_id: Mapped[uuid.UUID | None] = uuid_column(nullable=True)
    lock_id: Mapped[uuid.UUID | None] = uuid_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class CertificateRow(Base):
    """`certificates` (insert-only): every certificate received, failed ones kept (SPEC_DECISIONS 61)."""

    __tablename__ = "certificates"
    __table_args__ = (
        Index(
            "uq_certificates_delivery",
            "certificate_id",
            "body_hash",
            "signature",
            text("md5(body::text)"),
            unique=True,
        ),
        Index(
            "uq_certificates_verified_certificate_id",
            "certificate_id",
            unique=True,
            postgresql_where=text("verified"),
        ),
        Index(
            "uq_certificates_verified_request_id",
            "request_id",
            unique=True,
            postgresql_where=text("verified"),
        ),
        Index("ix_certificates_request_id", "request_id"),
    )

    certificate_row_id: Mapped[uuid.UUID] = uuid_pk()
    certificate_id: Mapped[uuid.UUID] = uuid_column()
    request_id: Mapped[uuid.UUID] = uuid_fk("registry_requests.request_id")
    registry_submission_id: Mapped[uuid.UUID] = uuid_column()
    kind: Mapped[str] = mapped_column(Text)
    as_of_seq: Mapped[int] = mapped_column(BigInteger)
    issued_at: Mapped[datetime] = timestamp()
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    body_hash: Mapped[str] = hash_column()
    signature: Mapped[str] = mapped_column(Text)
    signing_key_id: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean)
    verified_at: Mapped[datetime] = timestamp()
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = timestamp()


class CheckpointSeenRow(Base):
    """`checkpoints_seen` (insert-only): every signed checkpoint received, failed ones kept as evidence."""

    __tablename__ = "checkpoints_seen"
    __table_args__ = (
        UniqueConstraint("checkpoint_seq", "head_hash", "signature", name="uq_checkpoints_seen_delivery"),
        Index(
            "uq_checkpoints_seen_verified_seq",
            "checkpoint_seq",
            unique=True,
            postgresql_where=text("verified"),
        ),
    )

    checkpoint_id: Mapped[uuid.UUID] = uuid_pk()
    checkpoint_seq: Mapped[int] = mapped_column(BigInteger)
    last_event_seq: Mapped[int] = mapped_column(BigInteger)
    head_hash: Mapped[str] = hash_column()
    signed_at: Mapped[datetime] = timestamp()
    signature: Mapped[str] = mapped_column(Text)
    signing_key_id: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = timestamp()
    verified: Mapped[bool] = mapped_column(Boolean)
    verified_at: Mapped[datetime] = timestamp()
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_bad_event_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AlertRow(Base):
    """`alerts`: this bank's side of a registry case on one of its loans."""

    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("registry_case_id", "loan_id", name="uq_alerts_case_loan"),
        Index("ix_alerts_status_received_at", "status", "received_at"),
        Index("ix_alerts_loan_id", "loan_id"),
    )

    alert_id: Mapped[uuid.UUID] = uuid_pk()
    registry_case_id: Mapped[uuid.UUID] = uuid_column()
    loan_id: Mapped[uuid.UUID] = uuid_fk("loans.loan_id")
    role: Mapped[str] = mapped_column(Text)
    match_kind: Mapped[str] = mapped_column(Text)
    match_basis: Mapped[str] = mapped_column(Text)
    kinds_matched: Mapped[list[str]] = mapped_column(ARRAY(Text))
    kinds_not_matched: Mapped[list[str]] = mapped_column(ARRAY(Text))
    explanation_text: Mapped[str] = mapped_column(Text)
    explanation_source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    case_status: Mapped[str] = mapped_column(Text)
    bank_position: Mapped[str | None] = mapped_column(Text, nullable=True)
    position_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = timestamp()
    reviewed_by: Mapped[uuid.UUID | None] = uuid_fk("users.user_id", nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
