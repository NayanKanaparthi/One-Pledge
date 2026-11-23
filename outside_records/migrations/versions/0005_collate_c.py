"""Every text-typed column compares and orders by code point: COLLATE "C" (SPEC_DECISIONS 133.2).

Under UTF8, "C" orders by the bytes of the encoding, which is code point order, the order
Python uses; and it is deterministic, so equality, the unique keys and the pattern checks
do not depend on the database's default collation. `alembic_version.version_num` is
included so that no text column of the schema is left on the default.

Revision ID: 0005_collate_c
Revises: 0004_alembic_version_grant
"""

from __future__ import annotations

from alembic import op

revision = "0005_collate_c"
down_revision = "0004_alembic_version_grant"
branch_labels = None
depends_on = None

# Table, column and declared type of every text-typed column.
TEXT_COLUMNS = (
    ("title_records", "vin", "char(17)"),
    ("title_records", "state", "char(2)"),
    ("title_records", "title_status", "text"),
    ("title_records", "lienholder_name", "text"),
    ("payments", "vin", "char(17)"),
    ("payments", "deposit_bank_code", "text"),
    ("contracts", "vin", "char(17)"),
    ("contracts", "lender_name", "text"),
    ("contracts", "document_sha256", "char(64)"),
    ("api_clients", "name", "text"),
    ("api_clients", "api_key_sha256", "char(64)"),
    ("alembic_version", "version_num", "varchar(32)"),
)


def _collate(collation: str) -> None:
    for table, column, kind in TEXT_COLUMNS:
        op.execute(f'ALTER TABLE {table} ALTER COLUMN {column} TYPE {kind} COLLATE "{collation}"')


def upgrade() -> None:
    """Set COLLATE "C" on every text-typed column."""
    _collate("C")


def downgrade() -> None:
    """Back to the database default."""
    _collate("default")
