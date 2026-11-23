"""A quarantined payload the bank cannot store is kept as its hash alone (SPEC_DECISIONS 85).

PostgreSQL refuses U+0000 in `jsonb`, and a surrogate code point has no UTF-8 form, so a
registry message holding either could not be quarantined: the insert failed, the message
was never acknowledged and blocked the outbox. Such a payload is now withheld like an
oversized one: `payload` is NULL and `payload_sha256` is kept. The check
`ck_registry_messages_quarantined_payload` therefore changes from "NULL exactly when
OVERSIZED" to "OVERSIZED always NULL"; a payload of any other reason is NULL only when the
bank withheld it.

Revision ID: 0011_withheld_payload
Revises: 0010_column_guards
"""

from __future__ import annotations

from alembic import op

revision = "0011_withheld_payload"
down_revision = "0010_column_guards"
branch_labels = None
depends_on = None

QUARANTINE = "registry_messages_quarantined"
CHECK = "ck_registry_messages_quarantined_payload"


def upgrade() -> None:
    """An OVERSIZED payload is always withheld; any other may be."""
    op.drop_constraint(CHECK, QUARANTINE, type_="check")
    op.create_check_constraint(CHECK, QUARANTINE, "reason <> 'OVERSIZED' OR payload IS NULL")


def downgrade() -> None:
    """Back to "NULL exactly when OVERSIZED"; a withheld payload of another reason stops it."""
    op.drop_constraint(CHECK, QUARANTINE, type_="check")
    op.create_check_constraint(CHECK, QUARANTINE, "(reason = 'OVERSIZED') = (payload IS NULL)")
