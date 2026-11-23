"""The contract's rules as check constraints, so no row can make the API break its contract.

SPEC_DECISIONS 109.5 (adds to SPEC 6.1): the VIN pattern on every table, the state pattern,
the name lengths and the absence of control characters, the bank code pattern and the date
bounds of decision 100. The unique keys (payment_id, contract_id, one title per VIN) are the
primary keys of 0001. `seed` checks the same rules before writing (domain.record_rules).

Revision ID: 0003_contract_checks
Revises: 0002_grants
"""

from __future__ import annotations

from alembic import op

from onepledge_outside_records.infrastructure.db.models import CONTRACT_CHECKS

revision = "0003_contract_checks"
down_revision = "0002_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add every check of CONTRACT_CHECKS."""
    for table, checks in CONTRACT_CHECKS.items():
        for name, condition in checks.items():
            op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    """Drop them again."""
    for table, checks in CONTRACT_CHECKS.items():
        for name in checks:
            op.drop_constraint(name, table, type_="check")
