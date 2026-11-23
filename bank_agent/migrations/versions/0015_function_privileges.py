"""No function of the schema is executable by PUBLIC (SPEC_DECISIONS 103, point 8).

PostgreSQL gives `EXECUTE` on a new function to `PUBLIC`, so the app role could call every guard
function of the schema directly. Trigger functions do not need the privilege to fire, so it is
taken away from `PUBLIC` on every function of `public`; a later migration that creates a function
must do the same (the least-privilege test of a freshly migrated database checks it).

Revision ID: 0015_function_privileges
Revises: 0014_no_delete
"""

from __future__ import annotations

from alembic import op

revision = "0015_function_privileges"
down_revision = "0014_no_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Revoke EXECUTE on every function of `public` from PUBLIC."""
    op.execute("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")


def downgrade() -> None:
    """Give it back, as PostgreSQL's default had it."""
    op.execute("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO PUBLIC")
