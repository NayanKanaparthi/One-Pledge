"""The four tables of SPEC 4.1: key_versions, evaluation_log, rate_limits, api_credentials.

No table has a column for an element, a token or the scalar (decision 12).

Revision ID: 0001_schema
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the four tables with their checks."""
    op.create_table(
        "key_versions",
        sa.Column("key_version", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("key_version >= 1", name="ck_key_versions_key_version"),
        sa.CheckConstraint("status IN ('ACTIVE', 'RETIRED_READABLE')", name="ck_key_versions_status"),
    )
    op.create_index(
        "uq_key_versions_one_active",
        "key_versions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "evaluation_log",
        sa.Column("eval_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("item_count BETWEEN 1 AND 1000", name="ck_evaluation_log_item_count"),
    )
    op.create_table(
        "rate_limits",
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("items_used", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("member_id", "window_start", name="pk_rate_limits"),
        sa.CheckConstraint("items_used >= 0", name="ck_rate_limits_items_used"),
    )
    op.create_table(
        "api_credentials",
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_sha256", sa.CHAR(64), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("key_sha256 ~ '^[0-9a-f]{64}$'", name="ck_api_credentials_key_sha256"),
    )


def downgrade() -> None:
    """Drop the four tables."""
    op.drop_table("api_credentials")
    op.drop_table("rate_limits")
    op.drop_table("evaluation_log")
    op.drop_index("uq_key_versions_one_active", table_name="key_versions")
    op.drop_table("key_versions")
