"""The app role's privileges are exactly the grant matrix (SPEC_DECISIONS 72).

Every table of `public` gets the privileges `GRANT_MATRIX` names for it and nothing else
(`infrastructure/db/role_check.py`, the same table the startup guard and the integrity
report compare with), and the app role loses its USAGE on the `login_attempts` identity
sequence: inserting into an identity column needs no sequence privilege, and decision 72
allows none.

Revision ID: 0009_grant_matrix
Revises: 0008_outbox_poison
"""

from __future__ import annotations

import re

from alembic import context, op
from alembic.util import CommandError

from onepledge_bank_agent.infrastructure.db.role_check import apply_grants

revision = "0009_grant_matrix"
down_revision = "0008_outbox_poison"
branch_labels = None
depends_on = None

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SEQUENCE = "login_attempts_attempt_id_seq"


def _app_role() -> str:
    role = context.config.attributes.get("app_role")
    if not isinstance(role, str) or not _ROLE_NAME.fullmatch(role):
        raise CommandError("DB_APP_ROLE must name the app role created by db provision.")
    return role


def upgrade() -> None:
    """Apply the matrix to every table and revoke every sequence privilege."""
    apply_grants(op.get_bind(), _app_role())


def downgrade() -> None:
    """Give back the sequence USAGE of 0003; the table grants are those of 0003 and 0008 already."""
    op.execute(f'GRANT USAGE ON SEQUENCE {SEQUENCE} TO "{_app_role()}"')
