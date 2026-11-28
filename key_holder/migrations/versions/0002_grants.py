"""The app role's privileges (SPEC 4.1): it reads credentials and key versions, counts, and appends.

The app role is `{database}_app`, the role `db provision` creates. It may insert into
`evaluation_log` and nothing else there: no SELECT, no UPDATE, no DELETE. On `rate_limits`
it may select, insert and update, which is what counting needs.

Revision ID: 0002_grants
Revises: 0001_schema
"""

from __future__ import annotations

import re

from alembic import op
from alembic.util import CommandError
from sqlalchemy import text

revision = "0002_grants"
down_revision = "0001_schema"
branch_labels = None
depends_on = None

GRANTS = {
    "api_credentials": ("SELECT",),
    "key_versions": ("SELECT",),
    "evaluation_log": ("INSERT",),
    "rate_limits": ("SELECT", "INSERT", "UPDATE"),
}
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def app_role() -> str:
    """`{database}_app`, which must exist."""
    bind = op.get_bind()
    role = str(bind.execute(text("SELECT current_database() || '_app'")).scalar_one())
    found = bind.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}).first()
    if not _ROLE_NAME.fullmatch(role) or found is None:
        raise CommandError("The app role {database}_app does not exist; run db provision first.")
    return role


def upgrade() -> None:
    """Grant exactly the privileges of GRANTS."""
    role = app_role()
    for table, privileges in GRANTS.items():
        op.execute(f'GRANT {", ".join(privileges)} ON TABLE {table} TO "{role}"')


def downgrade() -> None:
    """Revoke every privilege of the app role on the four tables."""
    role = app_role()
    for table in GRANTS:
        op.execute(f'REVOKE ALL ON TABLE {table} FROM "{role}"')
