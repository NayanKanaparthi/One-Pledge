"""Values from another party stay inside what every reader can read back (SPEC_DECISIONS 100).

A timestamp or a date that another party supplied is checked by the database with the same
bounds as the bank's value objects (`domain/storable.py`), so no table, evidence tables
included, can hold a value the driver cannot read:

- `certificates.issued_at`, `checkpoints_seen.signed_at`: from 2000-01-01T00:00:00Z up to, not
  including, 2100-01-01T00:00:00Z;
- `tape_submissions.as_of_date`, `loans.origination_date`, `facilities.opened_on`: from
  2000-01-01 to 2099-12-31.

Revision ID: 0012_round_trip
Revises: 0011_withheld_payload
"""

from __future__ import annotations

from alembic import op

revision = "0012_round_trip"
down_revision = "0011_withheld_payload"
branch_labels = None
depends_on = None

INSTANT = (
    "{column} >= TIMESTAMPTZ '2000-01-01 00:00:00+00' AND {column} < TIMESTAMPTZ '2100-01-01 00:00:00+00'"
)
DAY = "{column} BETWEEN DATE '2000-01-01' AND DATE '2099-12-31'"
CHECKS = (
    ("certificates", "issued_at", INSTANT),
    ("checkpoints_seen", "signed_at", INSTANT),
    ("tape_submissions", "as_of_date", DAY),
    ("loans", "origination_date", DAY),
    ("facilities", "opened_on", DAY),
)


def upgrade() -> None:
    """One check constraint per column, named `ck_<table>_<column>_range`."""
    for table, column, rule in CHECKS:
        op.create_check_constraint(f"ck_{table}_{column}_range", table, rule.format(column=column))


def downgrade() -> None:
    """Drop the five checks."""
    for table, column, _ in CHECKS:
        op.drop_constraint(f"ck_{table}_{column}_range", table, type_="check")
