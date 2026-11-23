"""The outbox can neither block nor lose a message (SPEC_DECISIONS 101).

1. `registry_outbox_attempts` is replaced by `registry_outbox_messages`: one row per message
   with a usable id, how it was handled (`APPLIED`, `IGNORED`, `QUARANTINED`, `APPLIED_LATE`)
   and whether the registry took the acknowledgement (`PENDING`, `ACKED`, `REFUSED`). Its guard
   `onepledge_outbox_ledger_guard()` keeps `message_id`, `message_type` and `first_seen_at`,
   sets `outcome` once (a quarantined case update may become `APPLIED_LATE`) (SQLSTATE `OP009`).
   Deletion is refused for this table like every other by migration 0014 (decision 106).
2. `registry_outbox_state`: one row (`singleton` is always true) with the last complete sync,
   since when syncs are incomplete and why, and since when the bank is blind.
3. `registry_messages_quarantined`: `payload_withheld` (true exactly when `payload` is NULL;
   always for OVERSIZED); `message_id` NULL exactly for an UNACKNOWLEDGEABLE element, which is
   kept once by its payload hash; reasons of decision 101 (`REPEATED_FAILURE` withdrawn,
   `UNACKNOWLEDGEABLE` and `UNMATCHED` added).
4. Audit actions `CASE_UPDATE_UNMATCHED`, `REGISTRY_ACK_REFUSED`, `REGISTRY_OUTBOX_BLIND`,
   `REGISTRY_OUTBOX_RECOVERED`.

Revision ID: 0013_outbox_ledger
Revises: 0012_round_trip
"""

from __future__ import annotations

import importlib
import re

import sqlalchemy as sa
from alembic import context, op
from alembic.util import CommandError
from sqlalchemy.dialects import postgresql

from onepledge_bank_agent.infrastructure.db.role_check import apply_grants

revision = "0013_outbox_ledger"
down_revision = "0012_round_trip"
branch_labels = None
depends_on = None

QUARANTINE = "registry_messages_quarantined"
LEDGER = "registry_outbox_messages"
STATE = "registry_outbox_state"
ATTEMPTS = "registry_outbox_attempts"
REASONS = (
    "SCHEMA_MISMATCH", "VALUE_OUT_OF_RANGE", "CONTRADICTS_STORED_STATE", "REFUSED_BY_DATABASE",
    "REJECTED_BY_DEPENDENCY", "UNACKNOWLEDGEABLE", "UNMATCHED", "OVERSIZED", "UNEXPECTED_ERROR",
)  # fmt: skip
NEW_ACTIONS = (
    "CASE_UPDATE_UNMATCHED",
    "REGISTRY_ACK_REFUSED",
    "REGISTRY_OUTBOX_BLIND",
    "REGISTRY_OUTBOX_RECOVERED",
)
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

LEDGER_GUARD = """
CREATE FUNCTION onepledge_outbox_ledger_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.message_id IS DISTINCT FROM OLD.message_id
       OR NEW.message_type IS DISTINCT FROM OLD.message_type
       OR NEW.first_seen_at IS DISTINCT FROM OLD.first_seen_at THEN
        RAISE EXCEPTION 'the identity of an outbox message is immutable' USING ERRCODE = 'OP009';
    END IF;
    IF OLD.outcome IS NOT NULL AND NEW.outcome IS DISTINCT FROM OLD.outcome
       AND NOT (OLD.outcome = 'QUARANTINED' AND NEW.outcome = 'APPLIED_LATE') THEN
        RAISE EXCEPTION 'the outcome of an outbox message is set once' USING ERRCODE = 'OP009';
    END IF;
    RETURN NEW;
END;
$$
"""


def _earlier(module: str, name: str) -> tuple[str, ...]:
    found = vars(importlib.import_module(f"onepledge_bank_agent_migrations.versions.{module}"))[name]
    return tuple(found)


def _app_role() -> str:
    role = context.config.attributes.get("app_role")
    if not isinstance(role, str) or not _ROLE_NAME.fullmatch(role):
        raise CommandError("DB_APP_ROLE must name the app role created by db provision.")
    return role


def _listed(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _check(table: str, name: str, rule: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, rule)


def _ledger_and_state() -> None:
    op.create_table(
        LEDGER,
        sa.Column("message_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_type", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("ack_status", sa.Text(), nullable=False),
        sa.Column("last_reason", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "message_type ~ '^[A-Z][A-Z0-9_]{0,31}$' OR message_type = 'UNREADABLE'",
            name="ck_registry_outbox_messages_message_type",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('APPLIED', 'IGNORED', 'QUARANTINED', 'APPLIED_LATE')",
            name="ck_registry_outbox_messages_outcome",
        ),
        sa.CheckConstraint(
            "ack_status IN ('PENDING', 'ACKED', 'REFUSED')", name="ck_registry_outbox_messages_ack_status"
        ),
        sa.CheckConstraint(
            "(outcome IS NULL) = (handled_at IS NULL)", name="ck_registry_outbox_messages_handled"
        ),
    )
    op.execute(LEDGER_GUARD)
    op.execute(
        f"CREATE TRIGGER {LEDGER}_guard BEFORE UPDATE ON {LEDGER} "
        "FOR EACH ROW EXECUTE FUNCTION onepledge_outbox_ledger_guard()"
    )
    op.create_table(
        STATE,
        sa.Column("singleton", sa.Boolean(), primary_key=True),
        sa.Column("last_complete_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("incomplete_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reason", sa.Text(), nullable=True),
        sa.Column("blind_since", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("singleton", name="ck_registry_outbox_state_singleton"),
    )
    apply_grants(op.get_bind(), _app_role())  # the matrix gives both SELECT, INSERT, UPDATE


def _quarantine() -> None:
    op.add_column(QUARANTINE, sa.Column("payload_withheld", sa.Boolean(), nullable=True))
    op.execute(f"ALTER TABLE {QUARANTINE} DISABLE TRIGGER {QUARANTINE}_insert_only")
    op.execute("UPDATE registry_messages_quarantined SET payload_withheld = (payload IS NULL)")
    op.execute(f"ALTER TABLE {QUARANTINE} ENABLE TRIGGER {QUARANTINE}_insert_only")
    op.alter_column(QUARANTINE, "payload_withheld", nullable=False)
    op.alter_column(QUARANTINE, "message_id", nullable=True)
    _check(QUARANTINE, "ck_registry_messages_quarantined_reason", f"reason IN ({_listed(REASONS)})")
    _check(
        QUARANTINE,
        "ck_registry_messages_quarantined_payload",
        "payload_withheld = (payload IS NULL) AND (reason <> 'OVERSIZED' OR payload_withheld)",
    )
    op.create_check_constraint(
        "ck_registry_messages_quarantined_message_id",
        QUARANTINE,
        "(message_id IS NULL) = (reason = 'UNACKNOWLEDGEABLE')",
    )
    op.create_index(
        "uq_registry_messages_quarantined_element",
        QUARANTINE,
        ["payload_sha256"],
        unique=True,
        postgresql_where=sa.text("message_id IS NULL"),
    )


def upgrade() -> None:
    """Replace the attempts table, add the ledger, the state and the quarantine columns."""
    op.drop_table(ATTEMPTS)
    _ledger_and_state()
    _quarantine()
    actions = (*_earlier("0006_quarantine", "ACTIONS"), *NEW_ACTIONS)
    _check("audit_log", "ck_audit_log_action", f"action IN ({_listed(actions)})")


def downgrade() -> None:
    """Back to 0012; quarantine rows of the new reasons or without a message id stop it."""
    _check(
        "audit_log", "ck_audit_log_action", f"action IN ({_listed(_earlier('0006_quarantine', 'ACTIONS'))})"
    )
    op.drop_index("uq_registry_messages_quarantined_element", QUARANTINE)
    op.drop_constraint("ck_registry_messages_quarantined_message_id", QUARANTINE, type_="check")
    _check(QUARANTINE, "ck_registry_messages_quarantined_payload", "reason <> 'OVERSIZED' OR payload IS NULL")
    _check(
        QUARANTINE,
        "ck_registry_messages_quarantined_reason",
        f"reason IN ({_listed(_earlier('0008_outbox_poison', 'REASONS'))})",
    )
    op.alter_column(QUARANTINE, "message_id", nullable=False)
    op.drop_column(QUARANTINE, "payload_withheld")
    op.drop_table(STATE)
    op.drop_table(LEDGER)
    op.execute("DROP FUNCTION onepledge_outbox_ledger_guard()")
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
