"""SELECT on the four tables for the app role, and nothing else (SPEC 6.1).

The app role name comes from DB_APP_ROLE (through the migration runner). The
migration stops with a clear message when it is unset or the role does not exist.

Revision ID: 0002_grants
Revises: 0001_schema
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import context, op
from alembic.util import CommandError

revision = "0002_grants"
down_revision = "0001_schema"
branch_labels = None
depends_on = None

TABLES = ("title_records", "payments", "contracts", "api_clients")
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
    """Grant SELECT only."""
    role = _app_role()
    for table in TABLES:
        op.execute(f'GRANT SELECT ON TABLE {table} TO "{role}"')


def downgrade() -> None:
    """Revoke every privilege of the app role on the four tables."""
    role = _app_role()
    for table in TABLES:
        op.execute(f'REVOKE ALL ON TABLE {table} FROM "{role}"')
