"""The signed body of every received checkpoint, exactly as received (an addition to SPEC 12.1).

A checkpoint's signature is over the canonical JSON of its body. The columns of SPEC
12.1 hold the body's fields, but `signed_at` as a timestamp cannot give back the exact
text that was signed, and a field added to the body later would be lost. Deep
verification re-verifies every stored signature (SPEC 15.6 step 4), so the body is
kept as received, like `certificates.body`. The column is covered by the insert-only
trigger of `checkpoints_seen`.

Revision ID: 0004_checkpoint_body
Revises: 0003_grants
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_checkpoint_body"
down_revision = "0003_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add `checkpoints_seen.body`, a JSON object."""
    op.add_column("checkpoints_seen", sa.Column("body", postgresql.JSONB(), nullable=False))
    op.create_check_constraint(
        "ck_checkpoints_seen_body", "checkpoints_seen", "jsonb_typeof(body) = 'object'"
    )


def downgrade() -> None:
    """Drop `checkpoints_seen.body`."""
    op.drop_constraint("ck_checkpoints_seen_body", "checkpoints_seen", type_="check")
    op.drop_column("checkpoints_seen", "body")
