"""Status and case status move together; what may change on a row, column by column (SPEC_DECISIONS 86, 87).

1. `alerts` gets the check constraint `ck_alerts_status_case_status`: NEW and REVIEWING
   only with an OPEN case, CLEARED only with a `CLEARED_*` case, ESCALATED only with
   CONFIRMED. The alerts guard refuses the same pairs first, with OP006, and now also
   keeps `explanation_text` and `explanation_source` and sets `position_submitted_at`
   once, while REVIEWING.
2. `onepledge_loans_guard()`: `loan_id`, `facility_id`, `vin`, `first_submission_id` and
   `created_at` never change (OP003); borrower, origination date, amount and loan number
   change only while the loan has no `loan_tokens` row (OP003); a RELEASED loan never
   changes (OP002).
3. `onepledge_tape_guard()`: a COMPLETED tape never changes, and while FAILED
   `attempts` and `next_attempt_at` stay as they are (OP004); the rest is decision 46.

The column classes are `infrastructure/db/guard_columns.py`.

Revision ID: 0010_column_guards
Revises: 0009_grant_matrix
"""

from __future__ import annotations

import importlib

from alembic import op

revision = "0010_column_guards"
down_revision = "0009_grant_matrix"
branch_labels = None
depends_on = None

PAIRS = (
    "(status IN ('NEW', 'REVIEWING') AND case_status = 'OPEN') "
    "OR (status = 'CLEARED' AND case_status IN ('CLEARED_DATA_ERROR', 'CLEARED_LATE_RELEASE')) "
    "OR (status = 'ESCALATED' AND case_status = 'CONFIRMED')"
)

LOANS_GUARD = """
CREATE OR REPLACE FUNCTION onepledge_loans_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state = 'RELEASED' AND ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) THEN
        RAISE EXCEPTION 'a released loan never changes' USING ERRCODE = 'OP002';
    END IF;
    IF NEW.loan_id IS DISTINCT FROM OLD.loan_id
       OR NEW.facility_id IS DISTINCT FROM OLD.facility_id
       OR NEW.vin IS DISTINCT FROM OLD.vin
       OR NEW.first_submission_id IS DISTINCT FROM OLD.first_submission_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'loan identity is immutable' USING ERRCODE = 'OP003';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND (OLD.state, NEW.state) NOT IN (
        ('PENDING', 'HELD_LOCAL'), ('PENDING', 'LOCKED'), ('PENDING', 'CONFLICT'),
        ('HELD_LOCAL', 'PENDING'), ('CONFLICT', 'LOCKED'), ('LOCKED', 'RELEASED')
    ) THEN
        RAISE EXCEPTION 'loan state change % to % is not allowed', OLD.state, NEW.state
            USING ERRCODE = 'OP002';
    END IF;
    IF (NEW.borrower_ref IS DISTINCT FROM OLD.borrower_ref
        OR NEW.origination_date IS DISTINCT FROM OLD.origination_date
        OR NEW.original_amount_cents IS DISTINCT FROM OLD.original_amount_cents
        OR NEW.lender_loan_number IS DISTINCT FROM OLD.lender_loan_number)
       AND EXISTS (SELECT 1 FROM loan_tokens t WHERE t.loan_id = OLD.loan_id) THEN
        RAISE EXCEPTION 'the terms of a tokenised loan are immutable' USING ERRCODE = 'OP003';
    END IF;
    RETURN NEW;
END;
$$
"""

TAPE_FROZEN = """
    IF OLD.status = 'COMPLETED' AND ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) THEN
        RAISE EXCEPTION 'a completed tape submission never changes' USING ERRCODE = 'OP004';
    END IF;
    IF OLD.status = 'FAILED' AND NEW.status = 'FAILED'
       AND (NEW.attempts IS DISTINCT FROM OLD.attempts
            OR NEW.next_attempt_at IS DISTINCT FROM OLD.next_attempt_at) THEN
        RAISE EXCEPTION 'the attempts of a failed tape submission are kept' USING ERRCODE = 'OP004';
    END IF;
"""

ALERTS_GUARD = """
CREATE OR REPLACE FUNCTION onepledge_alerts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'NEW' OR NEW.case_status IS DISTINCT FROM 'OPEN'
           OR NEW.bank_position IS NOT NULL OR NEW.position_submitted_at IS NOT NULL
           OR NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL THEN
            RAISE EXCEPTION 'an alert is created NEW with its case OPEN' USING ERRCODE = 'OP006';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IN ('CLEARED', 'ESCALATED') AND ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) THEN
        RAISE EXCEPTION 'a % alert never changes', OLD.status USING ERRCODE = 'OP006';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND (OLD.status, NEW.status) NOT IN (
        ('NEW', 'REVIEWING'), ('NEW', 'CLEARED'), ('NEW', 'ESCALATED'),
        ('REVIEWING', 'CLEARED'), ('REVIEWING', 'ESCALATED')
    ) THEN
        RAISE EXCEPTION 'alert status change % to % is not allowed', OLD.status, NEW.status
            USING ERRCODE = 'OP006';
    END IF;
    IF NOT ((NEW.status IN ('NEW', 'REVIEWING') AND NEW.case_status = 'OPEN')
            OR (NEW.status = 'CLEARED' AND NEW.case_status IN ('CLEARED_DATA_ERROR', 'CLEARED_LATE_RELEASE'))
            OR (NEW.status = 'ESCALATED' AND NEW.case_status = 'CONFIRMED')) THEN
        RAISE EXCEPTION 'an alert closes only with its case' USING ERRCODE = 'OP006';
    END IF;
    IF NEW.alert_id IS DISTINCT FROM OLD.alert_id
       OR NEW.registry_case_id IS DISTINCT FROM OLD.registry_case_id
       OR NEW.loan_id IS DISTINCT FROM OLD.loan_id
       OR NEW.role IS DISTINCT FROM OLD.role
       OR NEW.match_kind IS DISTINCT FROM OLD.match_kind
       OR NEW.match_basis IS DISTINCT FROM OLD.match_basis
       OR NEW.kinds_matched IS DISTINCT FROM OLD.kinds_matched
       OR NEW.kinds_not_matched IS DISTINCT FROM OLD.kinds_not_matched
       OR NEW.received_at IS DISTINCT FROM OLD.received_at
       OR NEW.explanation_text IS DISTINCT FROM OLD.explanation_text
       OR NEW.explanation_source IS DISTINCT FROM OLD.explanation_source THEN
        RAISE EXCEPTION 'the facts of an alert are immutable' USING ERRCODE = 'OP006';
    END IF;
    IF NEW.bank_position IS DISTINCT FROM OLD.bank_position
       AND (OLD.bank_position IS NOT NULL OR OLD.status <> 'REVIEWING') THEN
        RAISE EXCEPTION 'a bank position is recorded once, while reviewing' USING ERRCODE = 'OP006';
    END IF;
    IF NEW.position_submitted_at IS DISTINCT FROM OLD.position_submitted_at
       AND (OLD.position_submitted_at IS NOT NULL OR OLD.status <> 'REVIEWING') THEN
        RAISE EXCEPTION 'a position is submitted once, while reviewing' USING ERRCODE = 'OP006';
    END IF;
    IF NEW.case_status IS DISTINCT FROM OLD.case_status AND OLD.case_status <> 'OPEN' THEN
        RAISE EXCEPTION 'a case leaves OPEN once' USING ERRCODE = 'OP006';
    END IF;
    RETURN NEW;
END;
$$
"""


def _earlier(revision_module: str, name: str) -> str:
    """A guard text of an earlier revision (a module name that starts with a digit is imported by name)."""
    module = importlib.import_module(f"onepledge_bank_agent_migrations.versions.{revision_module}")
    return str(vars(module)[name])


def _tape_guard() -> str:
    """Decision 46's tape guard with the frozen and failed rules ahead of it."""
    earlier = _earlier("0005_state_guards", "TAPE_GUARD")
    head, marker, rest = earlier.partition("BEGIN\n")
    return head + marker + TAPE_FROZEN + rest


def upgrade() -> None:
    """Replace the three guards and add the pair constraint."""
    op.execute(LOANS_GUARD)
    op.execute(_tape_guard())
    op.execute(ALERTS_GUARD)
    op.create_check_constraint("ck_alerts_status_case_status", "alerts", PAIRS)


def downgrade() -> None:
    """Put back the guards of 0002 and 0005 and drop the constraint."""
    op.drop_constraint("ck_alerts_status_case_status", "alerts", type_="check")
    previous_loans = _earlier("0002_guards", "LOANS_GUARD")
    op.execute(previous_loans.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute(_earlier("0005_state_guards", "TAPE_GUARD"))
    alerts = _earlier("0005_state_guards", "ALERTS_GUARD")
    op.execute(alerts.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
