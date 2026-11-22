"""`registry_messages_quarantined`: known outbox messages whose payload broke the contract.

SPEC_DECISIONS 57.

The 19th table of the bank database. A message of a known type whose payload does not
match the contract must neither block the outbox nor vanish: it is stored here as
evidence, audited and acknowledged. The payload is kept as received unless its
serialised form exceeds 64 KiB; then only `payload_sha256` is kept and `payload` is
null. Insert-only like the other evidence tables (statement trigger, SQLSTATE OP001);
the app role may SELECT and INSERT only. The audit action list gains
`REGISTRY_MESSAGE_QUARANTINED`.

Revision ID: 0006_quarantine
Revises: 0005_state_guards
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import context, op
from alembic.util import CommandError
from sqlalchemy.dialects import postgresql

revision = "0006_quarantine"
down_revision = "0005_state_guards"
branch_labels = None
depends_on = None

TABLE = "registry_messages_quarantined"
REASONS = ("SCHEMA_MISMATCH", "UNKNOWN_ASSET_TOKEN_FORMAT", "OVERSIZED")
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
PREVIOUS_ACTIONS = (
    "LOGIN_SUCCEEDED", "LOGIN_FAILED", "LOGIN_RATE_LIMITED", "ACCESS_DENIED", "USER_CREATED", "USER_UPDATED",
    "TAPE_UPLOADED", "TAPE_UPLOAD_REJECTED", "RELEASE_REQUESTED", "TAPE_STAGE_ADVANCED",
    "TAPE_STAGE_ATTEMPT_FAILED", "TAPE_FAILED", "TAPE_RETRY_REQUESTED", "LOAN_STATE_CHANGED",
    "REGISTRY_REQUEST_SENT", "REGISTRY_RESPONSE_RECEIVED", "REGISTRY_ITEM_REJECTED", "CERTIFICATE_VERIFIED",
    "CERTIFICATE_REJECTED", "ALERT_RECEIVED", "ALERT_UNMATCHED", "ALERT_REVIEW_STARTED",
    "ALERT_POSITION_SUBMITTED", "ALERT_CASE_UPDATED", "LOAN_RESUBMITTED", "CHECKPOINT_VERIFIED",
    "CHECKPOINT_REJECTED", "REGISTRY_MESSAGE_IGNORED", "REGISTRY_SYNC_REQUESTED",
    "INTEGRITY_VERIFICATION_RUN", "LINEAGE_VIEWED", "AUDIT_LOG_VIEWED", "HALT_RULE_DISABLED",
    "REFERENCE_DATA_SEEDED", "SCHEMA_MIGRATED",
)  # fmt: skip
ACTIONS = (*PREVIOUS_ACTIONS, "REGISTRY_MESSAGE_QUARANTINED")


def _app_role() -> str:
    role = context.config.attributes.get("app_role")
    if not isinstance(role, str) or not _ROLE_NAME.fullmatch(role):
        raise CommandError("DB_APP_ROLE must name the app role created by db provision.")
    return role


def _actions(actions: tuple[str, ...]) -> None:
    listed = ", ".join(f"'{action}'" for action in actions)
    op.drop_constraint("ck_audit_log_action", "audit_log", type_="check")
    op.create_check_constraint("ck_audit_log_action", "audit_log", f"action IN ({listed})")


def upgrade() -> None:
    """Create the table, its insert-only trigger and the app role's SELECT and INSERT."""
    _actions(ACTIONS)
    listed = ", ".join(f"'{reason}'" for reason in REASONS)
    op.create_table(
        TABLE,
        sa.Column("quarantine_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.Column("message_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("payload_sha256", sa.CHAR(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("message_id", name="uq_registry_messages_quarantined_message_id"),
        sa.CheckConstraint(f"reason IN ({listed})", name="ck_registry_messages_quarantined_reason"),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_registry_messages_quarantined_payload_sha256"
        ),
        sa.CheckConstraint("position >= 1", name="ck_registry_messages_quarantined_position"),
        sa.CheckConstraint(
            "length(message_type) BETWEEN 1 AND 64", name="ck_registry_messages_quarantined_message_type"
        ),
        sa.CheckConstraint(
            "(reason = 'OVERSIZED') = (payload IS NULL)", name="ck_registry_messages_quarantined_payload"
        ),
    )
    op.execute(
        f"CREATE TRIGGER {TABLE}_insert_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {TABLE} "
        "FOR EACH STATEMENT EXECUTE FUNCTION onepledge_forbid_mutation()"
    )
    op.execute(f'GRANT SELECT, INSERT ON TABLE {TABLE} TO "{_app_role()}"')


def downgrade() -> None:
    """Drop the table (a development operation: the evidence it holds goes with it)."""
    op.execute(f"DROP TRIGGER {TABLE}_insert_only ON {TABLE}")
    op.drop_table(TABLE)
    _actions(PREVIOUS_ACTIONS)
