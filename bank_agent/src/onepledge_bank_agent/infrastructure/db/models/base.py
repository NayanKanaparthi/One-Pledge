"""Declarative base and column helpers shared by the models.

The models declare what Alembic autogenerate compares: tables, columns, keys, foreign
keys, unique constraints and indexes. A test asserts they equal the migrated schema.
Every text-typed column declares `COLLATE "C"` (`C_TEXT`, `CHAR(n, collation="C")`), as
migration 0016 sets it. CHECK constraints and triggers live in the migrations only; another test reads them
from the live catalogue and compares every value list with its enum.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import CHAR

C_TEXT = Text(collation="C")
"""`text COLLATE "C"`: every text-typed column is compared and ordered by code point (SPEC_DECISIONS
133.2, migration 0016)."""


class Base(DeclarativeBase):
    """Declarative base of the 19 bank tables."""


def uuid_pk() -> Mapped[uuid.UUID]:
    """A UUID primary key column."""
    return mapped_column(UUID(as_uuid=True), primary_key=True)


def uuid_column(*, nullable: bool = False) -> Mapped[uuid.UUID]:
    """A UUID column that is not a key."""
    return mapped_column(UUID(as_uuid=True), nullable=nullable)


def uuid_fk(target: str, *, nullable: bool = False) -> Mapped[uuid.UUID]:
    """A UUID foreign key that restricts deletion."""
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete="RESTRICT"), nullable=nullable)


def timestamp() -> Mapped[datetime]:
    """A timezone aware timestamp."""
    return mapped_column(DateTime(timezone=True))


def hash_column() -> Mapped[str]:
    """A 64 character hash."""
    return mapped_column(CHAR(64, collation="C"))
