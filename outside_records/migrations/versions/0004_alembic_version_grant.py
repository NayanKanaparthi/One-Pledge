"""SELECT on `alembic_version` for the app role, so `serve` can check the revision (SPEC_DECISIONS 132).

SPEC 6.1 as amended: the app role holds SELECT on the four data tables and on
`alembic_version`, nothing else. The schema startup check reads `version_num` and passes
only at the head revision of the installed migrations, as the bank agent's does.

Revision ID: 0004_alembic_version_grant
Revises: 0003_contract_checks
"""

from __future__ import annotations

from alembic import op

from onepledge_outside_records.infrastructure.db.migrations import granted_role

revision = "0004_alembic_version_grant"
down_revision = "0003_contract_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Grant the matrix's one privilege on the revision table."""
    op.execute(f'GRANT SELECT ON TABLE alembic_version TO "{granted_role()}"')


def downgrade() -> None:
    """Revoke it again."""
    op.execute(f'REVOKE ALL ON TABLE alembic_version FROM "{granted_role()}"')
