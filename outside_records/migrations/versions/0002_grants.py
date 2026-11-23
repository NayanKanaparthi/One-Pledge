"""The app role's grants of SPEC 6.1: SELECT on the four data tables.

The app role name comes from DB_APP_ROLE (through the migration runner). The
migration stops with a clear message when it is unset or the role does not exist.
These are the data-table entries of `GRANT_MATRIX`, which the startup guard compares the
role with (SPEC_DECISIONS 72); the matrix's `alembic_version` entry is granted by 0004.

Revision ID: 0002_grants
Revises: 0001_schema
"""

from __future__ import annotations

from alembic import op

from onepledge_outside_records.infrastructure.db.migrations import granted_role
from onepledge_outside_records.infrastructure.db.role_check import GRANT_MATRIX

# The tables this revision grants on; `alembic_version` is 0004's (SPEC_DECISIONS 132).
DATA_TABLES = ("api_clients", "contracts", "payments", "title_records")

revision = "0002_grants"
down_revision = "0001_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Grant exactly what the matrix gives each table."""
    role = granted_role()
    for table in DATA_TABLES:
        for privilege in sorted(GRANT_MATRIX[table]):
            op.execute(f'GRANT {privilege} ON TABLE {table} TO "{role}"')


def downgrade() -> None:
    """Revoke every privilege of the app role on the tables of the matrix."""
    role = granted_role()
    for table in DATA_TABLES:
        op.execute(f'REVOKE ALL ON TABLE {table} FROM "{role}"')
