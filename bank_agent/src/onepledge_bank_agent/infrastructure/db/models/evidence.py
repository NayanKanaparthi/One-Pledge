"""`audit_log`, `integrity_runs` (SPEC 12.1), `registry_messages_quarantined` (SPEC_DECISIONS 57, 101),
`registry_outbox_messages` and `registry_outbox_state` (SPEC_DECISIONS 101).

The first three are insert-only; the outbox ledger and state are operational records, updated
under their own guard and never deleted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import CHAR

from onepledge_bank_agent.infrastructure.db.models.base import (
    C_TEXT,
    Base,
    hash_column,
    timestamp,
    uuid_column,
    uuid_fk,
    uuid_pk,
)


class AuditLogRow(Base):
    """`audit_log`: the bank's hash chained audit log."""

    __tablename__ = "audit_log"

    audit_id: Mapped[uuid.UUID] = uuid_pk()
    seq: Mapped[int] = mapped_column(BigInteger, unique=True)
    at: Mapped[datetime] = timestamp()
    actor_type: Mapped[str] = mapped_column(C_TEXT)
    actor_user_id: Mapped[uuid.UUID | None] = uuid_fk("users.user_id", nullable=True)
    action: Mapped[str] = mapped_column(C_TEXT)
    object_type: Mapped[str] = mapped_column(C_TEXT)
    object_id: Mapped[uuid.UUID | None] = uuid_column(nullable=True)
    outcome: Mapped[str] = mapped_column(C_TEXT)
    request_id: Mapped[str | None] = mapped_column(C_TEXT, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[str] = hash_column()
    entry_hash: Mapped[str] = mapped_column(CHAR(64, collation="C"), unique=True)


class IntegrityRunRow(Base):
    """`integrity_runs`: every deep verification and its report."""

    __tablename__ = "integrity_runs"

    run_id: Mapped[uuid.UUID] = uuid_pk()
    ran_at: Mapped[datetime] = timestamp()
    triggered_by: Mapped[uuid.UUID | None] = uuid_fk("users.user_id", nullable=True)
    status: Mapped[str] = mapped_column(C_TEXT)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)


class QuarantinedMessageRow(Base):
    """`registry_messages_quarantined`: a known outbox message whose payload broke the contract."""

    __tablename__ = "registry_messages_quarantined"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_registry_messages_quarantined_message_id"),
        Index(
            "uq_registry_messages_quarantined_element",
            "payload_sha256",
            unique=True,
            postgresql_where=text("message_id IS NULL"),
        ),
    )

    quarantine_id: Mapped[uuid.UUID] = uuid_pk()
    message_id: Mapped[uuid.UUID | None] = uuid_column(nullable=True)
    position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_type: Mapped[str] = mapped_column(C_TEXT)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=True)
    payload_withheld: Mapped[bool] = mapped_column(Boolean)
    payload_sha256: Mapped[str] = hash_column()
    reason: Mapped[str] = mapped_column(C_TEXT)
    received_at: Mapped[datetime] = timestamp()


class OutboxMessageRow(Base):
    """`registry_outbox_messages`: each outbox message with a usable id, how it was handled and acked."""

    __tablename__ = "registry_outbox_messages"

    message_id: Mapped[uuid.UUID] = uuid_pk()
    message_type: Mapped[str] = mapped_column(C_TEXT)
    first_seen_at: Mapped[datetime] = timestamp()
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(C_TEXT, nullable=True)
    ack_status: Mapped[str] = mapped_column(C_TEXT)
    last_reason: Mapped[str | None] = mapped_column(C_TEXT, nullable=True)
    updated_at: Mapped[datetime] = timestamp()


class OutboxStateRow(Base):
    """`registry_outbox_state`: the single row of outbox completeness and blindness."""

    __tablename__ = "registry_outbox_state"

    singleton: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    last_complete_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    incomplete_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reason: Mapped[str | None] = mapped_column(C_TEXT, nullable=True)
    blind_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
