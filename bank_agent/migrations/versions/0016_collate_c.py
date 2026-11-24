"""Every text-typed column is `COLLATE "C"` (SPEC_DECISIONS 133.2).

Every column of the bank's tables whose type is collatable (`text`, `char(n)`, `varchar`, `text[]`,
and `alembic_version.version_num`) gets the collation `"C"`: comparisons, unique keys, check
constraints with patterns and orderings on it are then by code point, the order Python uses,
whatever the database's default collation is. The columns are read from the catalogue of the
schema as 0015 leaves it, so none is missed; a column added later declares the collation itself,
and a test of the migrated catalogue fails on any other. The models declare the same collation.

Revision ID: 0016_collate_c
Revises: 0015_function_privileges
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0016_collate_c"
down_revision = "0015_function_privileges"
branch_labels = None
depends_on = None

_COLUMNS = text("""
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p') AND a.attnum > 0
    AND NOT a.attisdropped AND a.attcollation <> 0
""")


def _collate(collation: str) -> None:
    columns = sorted((str(t), str(c), str(k)) for t, c, k in op.get_bind().execute(_COLUMNS))
    for table, column, kind in columns:
        op.execute(f'ALTER TABLE public."{table}" ALTER COLUMN "{column}" TYPE {kind} COLLATE "{collation}"')


def upgrade() -> None:
    """`COLLATE "C"` on every collatable column of the schema."""
    _collate("C")


def downgrade() -> None:
    """Back to the database's default collation."""
    _collate("default")
