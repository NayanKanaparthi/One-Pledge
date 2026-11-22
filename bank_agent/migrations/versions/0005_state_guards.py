"""The state machines as the only path, on update and on insert (SPEC_DECISIONS 46 and 47).

1. `onepledge_tape_guard()` is tightened (decision 46): while the status is unchanged and
   not FAILED the three failure columns stay null; a forward step is exactly the next
   status of SPEC 8.3; entering FAILED records `resume_status = OLD.status`,
   `failed_stage` = the status after it and a failure code; while FAILED the three
   columns never change; leaving FAILED goes to `resume_status` and clears them. OP004.
2. `onepledge_tape_insert_guard()`: a submission is created RECEIVED, with no failure,
   completion or retry facts and no attempts. OP004.
3. `onepledge_loans_insert_guard()`: a loan is created PENDING. OP002.
4. `onepledge_alerts_guard()` on insert and update (SPEC 8.5): created NEW and OPEN with
   no review or position; only the transitions of SPEC 8.5; CLEARED and ESCALATED rows
   never change; the match facts, the loan, the case and `received_at` never change;
   `bank_position` is set once, while REVIEWING, and never changes after; `case_status`
   leaves OPEN once. OP006.

Revision ID: 0005_state_guards
Revises: 0004_checkpoint_body
"""

from __future__ import annotations

from alembic import op

revision = "0005_state_guards"
down_revision = "0004_checkpoint_body"
branch_labels = None
depends_on = None

TAPE_FACTS_UNCHANGED = """
    IF NEW.submission_id IS DISTINCT FROM OLD.submission_id
       OR NEW.facility_id IS DISTINCT FROM OLD.facility_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.file_name IS DISTINCT FROM OLD.file_name
       OR NEW.file_sha256 IS DISTINCT FROM OLD.file_sha256
       OR NEW.rows_sha256 IS DISTINCT FROM OLD.rows_sha256
       OR NEW.row_count IS DISTINCT FROM OLD.row_count
       OR NEW.as_of_date IS DISTINCT FROM OLD.as_of_date
       OR NEW.uploaded_by IS DISTINCT FROM OLD.uploaded_by
       OR NEW.uploaded_at IS DISTINCT FROM OLD.uploaded_at
       OR NEW.has_bom IS DISTINCT FROM OLD.has_bom
       OR NEW.header_text IS DISTINCT FROM OLD.header_text
       OR NEW.header_cells IS DISTINCT FROM OLD.header_cells
       OR NEW.trailer_text IS DISTINCT FROM OLD.trailer_text THEN
        RAISE EXCEPTION 'the facts of a tape upload are immutable' USING ERRCODE = 'OP004';
    END IF;
"""

TAPE_GUARD = (
    """
CREATE OR REPLACE FUNCTION onepledge_tape_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    following text := CASE OLD.status
        WHEN 'RECEIVED' THEN 'CLEANED' WHEN 'CLEANED' THEN 'CHECKED' WHEN 'CHECKED' THEN 'TOKENIZED'
        WHEN 'TOKENIZED' THEN 'SUBMITTED' WHEN 'SUBMITTED' THEN 'COMPLETED' END;
    no_failure boolean := NEW.resume_status IS NULL AND NEW.failed_stage IS NULL AND NEW.failure_code IS NULL;
    allowed boolean;
BEGIN
"""
    + TAPE_FACTS_UNCHANGED
    + """
    IF OLD.status = 'FAILED' AND NEW.status = 'FAILED' THEN
        allowed := NEW.resume_status IS NOT DISTINCT FROM OLD.resume_status
            AND NEW.failed_stage IS NOT DISTINCT FROM OLD.failed_stage
            AND NEW.failure_code IS NOT DISTINCT FROM OLD.failure_code;
    ELSIF OLD.status = 'FAILED' THEN
        allowed := NEW.status = OLD.resume_status AND no_failure;
    ELSIF NEW.status = 'FAILED' THEN
        allowed := following IS NOT NULL
            AND NEW.resume_status = OLD.status
            AND NEW.failed_stage = following
            AND NEW.failure_code IS NOT NULL;
    ELSIF NEW.status = OLD.status THEN
        allowed := no_failure;
    ELSE
        allowed := NEW.status = following AND no_failure;
    END IF;
    IF NOT COALESCE(allowed, false) THEN
        RAISE EXCEPTION 'tape status change % to % is not allowed', OLD.status, NEW.status
            USING ERRCODE = 'OP004';
    END IF;
    RETURN NEW;
END;
$$
"""
)

PREVIOUS_TAPE_GUARD = (
    """
CREATE OR REPLACE FUNCTION onepledge_tape_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
"""
    + TAPE_FACTS_UNCHANGED
    + """
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
        (OLD.status, NEW.status) IN (
            ('RECEIVED', 'CLEANED'), ('CLEANED', 'CHECKED'), ('CHECKED', 'TOKENIZED'),
            ('TOKENIZED', 'SUBMITTED'), ('SUBMITTED', 'COMPLETED')
        )
        OR (NEW.status = 'FAILED'
            AND OLD.status IN ('RECEIVED', 'CLEANED', 'CHECKED', 'TOKENIZED', 'SUBMITTED'))
        OR (OLD.status = 'FAILED' AND NEW.status = OLD.resume_status)
    ) THEN
        RAISE EXCEPTION 'tape status change % to % is not allowed', OLD.status, NEW.status
            USING ERRCODE = 'OP004';
    END IF;
    RETURN NEW;
END;
$$
"""
)

TAPE_INSERT_GUARD = """
CREATE FUNCTION onepledge_tape_insert_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM 'RECEIVED' OR NEW.resume_status IS NOT NULL
       OR NEW.failed_stage IS NOT NULL OR NEW.failure_code IS NOT NULL OR NEW.completed_at IS NOT NULL
       OR NEW.next_attempt_at IS NOT NULL OR NEW.attempts IS DISTINCT FROM 0 THEN
        RAISE EXCEPTION 'a tape submission is created RECEIVED' USING ERRCODE = 'OP004';
    END IF;
    RETURN NEW;
END;
$$
"""

LOANS_INSERT_GUARD = """
CREATE FUNCTION onepledge_loans_insert_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state IS DISTINCT FROM 'PENDING' THEN
        RAISE EXCEPTION 'a loan is created PENDING' USING ERRCODE = 'OP002';
    END IF;
    RETURN NEW;
END;
$$
"""

ALERTS_GUARD = """
CREATE FUNCTION onepledge_alerts_guard() RETURNS trigger
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
    IF NEW.alert_id IS DISTINCT FROM OLD.alert_id
       OR NEW.registry_case_id IS DISTINCT FROM OLD.registry_case_id
       OR NEW.loan_id IS DISTINCT FROM OLD.loan_id
       OR NEW.role IS DISTINCT FROM OLD.role
       OR NEW.match_kind IS DISTINCT FROM OLD.match_kind
       OR NEW.match_basis IS DISTINCT FROM OLD.match_basis
       OR NEW.kinds_matched IS DISTINCT FROM OLD.kinds_matched
       OR NEW.kinds_not_matched IS DISTINCT FROM OLD.kinds_not_matched
       OR NEW.received_at IS DISTINCT FROM OLD.received_at THEN
        RAISE EXCEPTION 'the facts of an alert are immutable' USING ERRCODE = 'OP006';
    END IF;
    IF NEW.bank_position IS DISTINCT FROM OLD.bank_position
       AND (OLD.bank_position IS NOT NULL OR OLD.status <> 'REVIEWING') THEN
        RAISE EXCEPTION 'a bank position is recorded once, while reviewing' USING ERRCODE = 'OP006';
    END IF;
    IF NEW.case_status IS DISTINCT FROM OLD.case_status AND OLD.case_status <> 'OPEN' THEN
        RAISE EXCEPTION 'a case leaves OPEN once' USING ERRCODE = 'OP006';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    """Replace the tape guard and add the insert guards and the alert guard."""
    for statement in (TAPE_GUARD, TAPE_INSERT_GUARD, LOANS_INSERT_GUARD, ALERTS_GUARD):
        op.execute(statement)
    op.execute(
        "CREATE TRIGGER tape_submissions_insert_guard BEFORE INSERT ON tape_submissions "
        "FOR EACH ROW EXECUTE FUNCTION onepledge_tape_insert_guard()"
    )
    op.execute(
        "CREATE TRIGGER loans_insert_guard BEFORE INSERT ON loans "
        "FOR EACH ROW EXECUTE FUNCTION onepledge_loans_insert_guard()"
    )
    op.execute(
        "CREATE TRIGGER alerts_guard BEFORE INSERT OR UPDATE ON alerts "
        "FOR EACH ROW EXECUTE FUNCTION onepledge_alerts_guard()"
    )


def downgrade() -> None:
    """Drop the new triggers and functions and restore the previous tape guard."""
    op.execute("DROP TRIGGER alerts_guard ON alerts")
    op.execute("DROP TRIGGER loans_insert_guard ON loans")
    op.execute("DROP TRIGGER tape_submissions_insert_guard ON tape_submissions")
    for function in ("onepledge_alerts_guard", "onepledge_loans_insert_guard", "onepledge_tape_insert_guard"):
        op.execute(f"DROP FUNCTION {function}()")
    op.execute(PREVIOUS_TAPE_GUARD)
