"""Least privilege for the app role (SPEC 12.2).

The app role may read and append to the evidence tables, read and update the working
tables, and only read the reference tables. It has no DELETE or TRUNCATE anywhere and
cannot run DDL. Its name comes from DB_APP_ROLE (through the migration runner); the
migration stops with a clear message when it is unset or the role does not exist.

Revision ID: 0003_grants
Revises: 0002_guards
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import context, op
from alembic.util import CommandError

revision = "0003_grants"
down_revision = "0002_guards"
branch_labels = None
depends_on = None

APPEND_ONLY = (
    "loans_raw", "audit_log", "cleaning_issues", "submission_items", "local_checks", "loan_tokens",
    "certificates", "checkpoints_seen", "integrity_runs", "login_attempts",
)  # fmt: skip
WORKING = ("tape_submissions", "loans", "registry_requests", "registry_request_items", "alerts", "users")
READ_ONLY = ("lenders", "facilities", "alembic_version")
SEQUENCES = ("login_attempts_attempt_id_seq",)
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _app_role() -> str:
    role = context.config.attributes.get("app_role")
    if not isinstance(role, str) or not _ROLE_NAME.fullmatch(role):
        raise CommandError("DB_APP_ROLE must name the app role created by db provision.")
    found = (
        op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}).first()
    )
    if found is None:
        raise CommandError("The role named by DB_APP_ROLE does not exist; run db provision first.")
    return role


def upgrade() -> None:
    """Grant exactly the privileges of the SPEC 12.2 table."""
    role = _app_role()
    for table in APPEND_ONLY:
        op.execute(f'GRANT SELECT, INSERT ON TABLE {table} TO "{role}"')
    for table in WORKING:
        op.execute(f'GRANT SELECT, INSERT, UPDATE ON TABLE {table} TO "{role}"')
    for table in READ_ONLY:
        op.execute(f'GRANT SELECT ON TABLE {table} TO "{role}"')
    for sequence in SEQUENCES:
        op.execute(f'GRANT USAGE ON SEQUENCE {sequence} TO "{role}"')


def downgrade() -> None:
    """Revoke every privilege of the app role."""
    role = _app_role()
    for table in (*APPEND_ONLY, *WORKING, *READ_ONLY):
        op.execute(f'REVOKE ALL ON TABLE {table} FROM "{role}"')
    for sequence in SEQUENCES:
        op.execute(f'REVOKE ALL ON SEQUENCE {sequence} FROM "{role}"')
