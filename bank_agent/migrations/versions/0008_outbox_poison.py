"""Poison registry messages are contained; deferrals are counted (SPEC_DECISIONS 85).

- New table `registry_outbox_attempts` (the 20th): one row per outbox message whose
  processing was deferred (a lost database connection, a serialisation failure or
  deadlock, a passing registry failure), with the number of deferrals, the last reason
  and when. At five deferrals the message is quarantined as `REPEATED_FAILURE`. Mutable
  by design: the app role may SELECT, INSERT and UPDATE; rows are never deleted.
- `registry_messages_quarantined.reason` takes the eight reasons of decision 85 rule 6
  (`UNKNOWN_ASSET_TOKEN_FORMAT` is now a `SCHEMA_MISMATCH`), and `position` may be null:
  a message whose position does not fit a `bigint` is still kept, without it.

Revision ID: 0008_outbox_poison
Revises: 0007_certificate_evidence
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import context, op
from alembic.util import CommandError
from sqlalchemy.dialects import postgresql

revision = "0008_outbox_poison"
down_revision = "0007_certificate_evidence"
branch_labels = None
depends_on = None

QUARANTINE = "registry_messages_quarantined"
ATTEMPTS = "registry_outbox_attempts"
REASONS = (
    "SCHEMA_MISMATCH", "VALUE_OUT_OF_RANGE", "CONTRADICTS_STORED_STATE", "REFUSED_BY_DATABASE",
    "REJECTED_BY_DEPENDENCY", "REPEATED_FAILURE", "OVERSIZED", "UNEXPECTED_ERROR",
)  # fmt: skip
PREVIOUS_REASONS = ("SCHEMA_MISMATCH", "UNKNOWN_ASSET_TOKEN_FORMAT", "OVERSIZED")
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _app_role() -> str:
    role = context.config.attributes.get("app_role")
    if not isinstance(role, str) or not _ROLE_NAME.fullmatch(role):
        raise CommandError("DB_APP_ROLE must name the app role created by db provision.")
    return role


def _reasons(reasons: tuple[str, ...]) -> None:
    listed = ", ".join(f"'{reason}'" for reason in reasons)
    op.drop_constraint("ck_registry_messages_quarantined_reason", QUARANTINE, type_="check")
    op.create_check_constraint("ck_registry_messages_quarantined_reason", QUARANTINE, f"reason IN ({listed})")


def upgrade() -> None:
    """Create the attempts table and widen the quarantine table's reasons and position."""
    _reasons(REASONS)
    op.drop_constraint("ck_registry_messages_quarantined_position", QUARANTINE, type_="check")
    op.alter_column(QUARANTINE, "position", nullable=True)
    op.create_check_constraint(
        "ck_registry_messages_quarantined_position", QUARANTINE, "position IS NULL OR position >= 1"
    )
    op.create_table(
        ATTEMPTS,
        sa.Column("message_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_reason", sa.Text(), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempts >= 1", name="ck_registry_outbox_attempts_attempts"),
        sa.CheckConstraint(
            "last_reason ~ '^[A-Z][A-Z0-9_]{0,63}$'", name="ck_registry_outbox_attempts_last_reason"
        ),
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON TABLE {ATTEMPTS} TO "{_app_role()}"')


def downgrade() -> None:
    """Drop the attempts table; quarantine rows outside the earlier reasons or without a position stop it."""
    op.drop_table(ATTEMPTS)
    op.drop_constraint("ck_registry_messages_quarantined_position", QUARANTINE, type_="check")
    op.alter_column(QUARANTINE, "position", nullable=False)
    op.create_check_constraint("ck_registry_messages_quarantined_position", QUARANTINE, "position >= 1")
    _reasons(PREVIOUS_REASONS)
