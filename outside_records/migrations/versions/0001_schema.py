"""The four tables of SPEC 6.1 with their checks and indexes.

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
    """Create title_records, payments, contracts and api_clients."""
    op.create_table(
        "title_records",
        sa.Column("vin", sa.CHAR(17), primary_key=True),
        sa.Column("state", sa.CHAR(2), nullable=False),
        sa.Column("title_status", sa.Text(), nullable=False),
        sa.Column("lienholder_name", sa.Text(), nullable=True),
        sa.Column("lien_recorded_on", sa.Date(), nullable=True),
        sa.CheckConstraint("vin ~ '^[A-HJ-NPR-Z0-9]{17}$'", name="ck_title_records_vin"),
        sa.CheckConstraint(
            "title_status IN ('CLEAN', 'SALVAGE', 'JUNK')", name="ck_title_records_title_status"
        ),
    )
    op.create_table(
        "payments",
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vin", sa.CHAR(17), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("deposit_bank_code", sa.Text(), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name="ck_payments_amount_cents"),
    )
    op.create_index("ix_payments_vin_paid_on", "payments", ["vin", sa.text("paid_on DESC"), "payment_id"])
    op.create_table(
        "contracts",
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vin", sa.CHAR(17), nullable=False),
        sa.Column("lender_name", sa.Text(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("signed_on", sa.Date(), nullable=False),
        sa.Column("document_sha256", sa.CHAR(64), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name="ck_contracts_amount_cents"),
        sa.CheckConstraint("document_sha256 ~ '^[0-9a-f]{64}$'", name="ck_contracts_document_sha256"),
    )
    op.create_index("ix_contracts_vin_signed_on", "contracts", ["vin", "signed_on", "contract_id"])
    op.create_table(
        "api_clients",
        sa.Column("client_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("api_key_sha256", sa.CHAR(64), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("api_key_sha256 ~ '^[0-9a-f]{64}$'", name="ck_api_clients_api_key_sha256"),
    )


def downgrade() -> None:
    """Drop the four tables."""
    op.drop_table("api_clients")
    op.drop_index("ix_contracts_vin_signed_on", table_name="contracts")
    op.drop_table("contracts")
    op.drop_index("ix_payments_vin_paid_on", table_name="payments")
    op.drop_table("payments")
    op.drop_table("title_records")
