"""The grant matrix equals SPEC 12.2 as amended (SPEC_DECISIONS 57, 72, 85), written out by hand here."""

from __future__ import annotations

import pytest

from onepledge_bank_agent.infrastructure.db.models import Base
from onepledge_bank_agent.infrastructure.db.role_check import GRANT_MATRIX

pytestmark = pytest.mark.unit

SPEC_12_2 = {
    # Evidence and append-only tables: read and append, never change.
    "loans_raw": {"SELECT", "INSERT"},
    "audit_log": {"SELECT", "INSERT"},
    "cleaning_issues": {"SELECT", "INSERT"},
    "submission_items": {"SELECT", "INSERT"},
    "local_checks": {"SELECT", "INSERT"},
    "loan_tokens": {"SELECT", "INSERT"},
    "certificates": {"SELECT", "INSERT"},
    "checkpoints_seen": {"SELECT", "INSERT"},
    "integrity_runs": {"SELECT", "INSERT"},
    "registry_messages_quarantined": {"SELECT", "INSERT"},
    "login_attempts": {"SELECT", "INSERT"},
    # Working tables: read, insert, update; never delete.
    "tape_submissions": {"SELECT", "INSERT", "UPDATE"},
    "loans": {"SELECT", "INSERT", "UPDATE"},
    "registry_requests": {"SELECT", "INSERT", "UPDATE"},
    "registry_request_items": {"SELECT", "INSERT", "UPDATE"},
    "alerts": {"SELECT", "INSERT", "UPDATE"},
    "users": {"SELECT", "INSERT", "UPDATE"},
    "registry_outbox_attempts": {"SELECT", "INSERT", "UPDATE"},
    # Reference data and the schema revision: read only.
    "lenders": {"SELECT"},
    "facilities": {"SELECT"},
    "alembic_version": {"SELECT"},
}


def test_the_matrix_is_the_specified_one() -> None:
    assert {table: set(privileges) for table, privileges in GRANT_MATRIX.items()} == SPEC_12_2


def test_every_table_of_the_models_is_in_the_matrix() -> None:
    assert set(Base.metadata.tables) | {"alembic_version"} == set(GRANT_MATRIX)
