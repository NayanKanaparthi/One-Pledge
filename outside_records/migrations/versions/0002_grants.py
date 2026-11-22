"""The app role's grants, read from the one grant matrix: SELECT on the four tables (SPEC 6.1).

The app role name comes from DB_APP_ROLE (through the migration runner). The
migration stops with a clear message when it is unset or the role does not exist.
The privileges are those of `GRANT_MATRIX`, which the startup guard also compares the
role with (SPEC_DECISIONS 72).

Revision ID: 0002_grants
Revises: 0001_schema
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import context, op
from alembic.util import CommandError

from onepledge_outside_records.infrastructure.db.role_check import GRANT_MATRIX

revision = "0002_grants"
down_revision = "0001_schema"
branch_labels = None
depends_on = None

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _app_role() -> str:
    role = context.config.attributes.get("app_role")
    if not isinstance(role, str) or not _ROLE_NAME.fullmatch(role):
        raise CommandError("DB_APP_ROLE must name the app role created by db provision.")
    exists = (
        op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}).first()
    )
    if exists is None:
        raise CommandError("The role named by DB_APP_ROLE does not exist; run db provision first.")
    return role


def upgrade() -> None:
    """Grant exactly what the matrix gives each table."""
    role = _app_role()
    for table, privileges in sorted(GRANT_MATRIX.items()):
        for privilege in sorted(privileges):
            op.execute(f'GRANT {privilege} ON TABLE {table} TO "{role}"')


def downgrade() -> None:
    """Revoke every privilege of the app role on the tables of the matrix."""
    role = _app_role()
    for table in sorted(GRANT_MATRIX):
        op.execute(f'REVOKE ALL ON TABLE {table} FROM "{role}"')
