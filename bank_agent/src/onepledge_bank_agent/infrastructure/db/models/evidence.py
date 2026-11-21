"""`audit_log` and `integrity_runs` (SPEC 12.1), both insert-only."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Text
from sqlalchemy.dialects.postgresql import JSONB
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


class AuditLogRow(Base):
    """`audit_log`: the bank's hash chained audit log."""

    __tablename__ = "audit_log"

    audit_id: Mapped[uuid.UUID] = uuid_pk()
    seq: Mapped[int] = mapped_column(BigInteger, unique=True)
    at: Mapped[datetime] = timestamp()
    actor_type: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID | None] = uuid_fk("users.user_id", nullable=True)
    action: Mapped[str] = mapped_column(Text)
    object_type: Mapped[str] = mapped_column(Text)
    object_id: Mapped[uuid.UUID | None] = uuid_column(nullable=True)
    outcome: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[str] = hash_column()
    entry_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)


class IntegrityRunRow(Base):
    """`integrity_runs`: every deep verification and its report."""

    __tablename__ = "integrity_runs"

    run_id: Mapped[uuid.UUID] = uuid_pk()
    ran_at: Mapped[datetime] = timestamp()
    triggered_by: Mapped[uuid.UUID | None] = uuid_fk("users.user_id", nullable=True)
    status: Mapped[str] = mapped_column(Text)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
