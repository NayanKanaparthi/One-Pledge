"""`tape_submissions`, `loans_raw`, `cleaning_issues`, `submission_items` (SPEC 12.1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from onepledge_bank_agent.infrastructure.db.models.base import (
    C_TEXT,
    Base,
    hash_column,
    timestamp,
    uuid_fk,
    uuid_pk,
)


class TapeSubmissionRow(Base):
    """`tape_submissions`."""

    __tablename__ = "tape_submissions"
    __table_args__ = (
        UniqueConstraint("facility_id", "kind", "file_sha256", name="uq_tape_submissions_file"),
        Index("ix_tape_submissions_status_uploaded_at", "status", "uploaded_at"),
        Index("ix_tape_submissions_facility_uploaded_at", "facility_id", "uploaded_at"),
    )

    submission_id: Mapped[uuid.UUID] = uuid_pk()
    facility_id: Mapped[uuid.UUID] = uuid_fk("facilities.facility_id")
    kind: Mapped[str] = mapped_column(C_TEXT)
    source: Mapped[str] = mapped_column(C_TEXT)
    file_name: Mapped[str] = mapped_column(C_TEXT)
    file_sha256: Mapped[str] = hash_column()
    rows_sha256: Mapped[str] = hash_column()
    has_bom: Mapped[bool] = mapped_column(Boolean)
    header_text: Mapped[str] = mapped_column(C_TEXT)
    header_cells: Mapped[list[str]] = mapped_column(JSONB)
    trailer_text: Mapped[str] = mapped_column(C_TEXT)
    as_of_date: Mapped[date] = mapped_column(Date)
    row_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(C_TEXT)
    resume_status: Mapped[str | None] = mapped_column(C_TEXT, nullable=True)
    failed_stage: Mapped[str | None] = mapped_column(C_TEXT, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(C_TEXT, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uploaded_by: Mapped[uuid.UUID] = uuid_fk("users.user_id")
    uploaded_at: Mapped[datetime] = timestamp()
    updated_at: Mapped[datetime] = timestamp()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RawRowRow(Base):
    """`loans_raw` (insert-only)."""

    __tablename__ = "loans_raw"
    __table_args__ = (UniqueConstraint("submission_id", "row_number", name="uq_loans_raw_submission_row"),)

    raw_id: Mapped[uuid.UUID] = uuid_pk()
    submission_id: Mapped[uuid.UUID] = uuid_fk("tape_submissions.submission_id")
    row_number: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(C_TEXT)
    raw_cells: Mapped[list[str]] = mapped_column(JSONB)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    row_sha256: Mapped[str] = hash_column()
    received_at: Mapped[datetime] = timestamp()


class CleaningIssueRow(Base):
    """`cleaning_issues` (insert-only)."""

    __tablename__ = "cleaning_issues"
    __table_args__ = (
        UniqueConstraint("raw_id", "field", "problem", name="uq_cleaning_issues_raw_field_problem"),
    )

    issue_id: Mapped[uuid.UUID] = uuid_pk()
    raw_id: Mapped[uuid.UUID] = uuid_fk("loans_raw.raw_id")
    field: Mapped[str] = mapped_column(C_TEXT)
    problem: Mapped[str] = mapped_column(C_TEXT)
    action: Mapped[str] = mapped_column(C_TEXT)


class SubmissionItemRow(Base):
    """`submission_items` (insert-only): the link from a raw row to a loan."""

    __tablename__ = "submission_items"
    __table_args__ = (
        UniqueConstraint("raw_id", name="uq_submission_items_raw_id"),
        UniqueConstraint("submission_id", "row_number", name="uq_submission_items_submission_row"),
        UniqueConstraint("submission_id", "loan_id", name="uq_submission_items_submission_loan"),
        Index("ix_submission_items_loan_id", "loan_id"),
    )

    item_id: Mapped[uuid.UUID] = uuid_pk()
    submission_id: Mapped[uuid.UUID] = uuid_fk("tape_submissions.submission_id")
    raw_id: Mapped[uuid.UUID] = uuid_fk("loans_raw.raw_id")
    row_number: Mapped[int] = mapped_column(Integer)
    disposition: Mapped[str] = mapped_column(C_TEXT)
    loan_id: Mapped[uuid.UUID | None] = uuid_fk("loans.loan_id", nullable=True)
