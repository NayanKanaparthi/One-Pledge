"""Integrity guards in the database itself (SPEC 12.4).

1. `onepledge_forbid_mutation()`: every insert-only table refuses UPDATE, DELETE and
   TRUNCATE with SQLSTATE OP001, for every role including the owner. The triggers are
   statement level, so a statement is refused even when it would touch no row.
2. `onepledge_loans_guard()`: a loan state change outside SPEC 8.4 raises OP002; a
   change of the token inputs of a tokenised loan, or of its identity, raises OP003.
3. `onepledge_tape_guard()`: a status change outside SPEC 8.3, or a change of an
   immutable fact of the upload, raises OP004.
4. `onepledge_audit_guard()`: an audit entry whose seq is not the next one, or whose
   prev_hash is not the previous entry's hash, raises OP005.

Revision ID: 0002_guards
Revises: 0001_schema
"""

from __future__ import annotations

from alembic import op

revision = "0002_guards"
down_revision = "0001_schema"
branch_labels = None
depends_on = None

INSERT_ONLY_TABLES = (
    "loans_raw",
    "audit_log",
    "cleaning_issues",
    "submission_items",
    "local_checks",
    "loan_tokens",
    "certificates",
    "checkpoints_seen",
    "integrity_runs",
)

FORBID_MUTATION = """
CREATE FUNCTION onepledge_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'table % is insert-only', TG_TABLE_NAME USING ERRCODE = 'OP001';
END;
$$
"""

LOANS_GUARD = """
CREATE FUNCTION onepledge_loans_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.loan_id IS DISTINCT FROM OLD.loan_id
       OR NEW.first_submission_id IS DISTINCT FROM OLD.first_submission_id THEN
        RAISE EXCEPTION 'loan identity is immutable' USING ERRCODE = 'OP003';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND (OLD.state, NEW.state) NOT IN (
        ('PENDING', 'HELD_LOCAL'), ('PENDING', 'LOCKED'), ('PENDING', 'CONFLICT'),
        ('HELD_LOCAL', 'PENDING'), ('CONFLICT', 'LOCKED'), ('LOCKED', 'RELEASED')
    ) THEN
        RAISE EXCEPTION 'loan state change % to % is not allowed', OLD.state, NEW.state
            USING ERRCODE = 'OP002';
    END IF;
    IF (NEW.vin IS DISTINCT FROM OLD.vin
        OR NEW.borrower_ref IS DISTINCT FROM OLD.borrower_ref
        OR NEW.origination_date IS DISTINCT FROM OLD.origination_date
        OR NEW.original_amount_cents IS DISTINCT FROM OLD.original_amount_cents
        OR NEW.facility_id IS DISTINCT FROM OLD.facility_id)
       AND EXISTS (SELECT 1 FROM loan_tokens t WHERE t.loan_id = OLD.loan_id) THEN
        RAISE EXCEPTION 'the terms of a tokenised loan are immutable' USING ERRCODE = 'OP003';
    END IF;
    RETURN NEW;
END;
$$
"""

TAPE_GUARD = """
CREATE FUNCTION onepledge_tape_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
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

AUDIT_GUARD = """
CREATE FUNCTION onepledge_audit_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    last_seq bigint;
    last_hash char(64);
BEGIN
    SELECT seq, entry_hash INTO last_seq, last_hash FROM audit_log ORDER BY seq DESC LIMIT 1;
    IF NEW.seq IS DISTINCT FROM COALESCE(last_seq, 0) + 1
       OR NEW.prev_hash IS DISTINCT FROM COALESCE(last_hash, repeat('0', 64)) THEN
        RAISE EXCEPTION 'audit entry does not extend the chain' USING ERRCODE = 'OP005';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    """Create the four guard functions and their triggers."""
    for statement in (FORBID_MUTATION, LOANS_GUARD, TAPE_GUARD, AUDIT_GUARD):
        op.execute(statement)
    for table in INSERT_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_insert_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION onepledge_forbid_mutation()"
        )
    op.execute(
        "CREATE TRIGGER loans_guard BEFORE UPDATE ON loans "
        "FOR EACH ROW EXECUTE FUNCTION onepledge_loans_guard()"
    )
    op.execute(
        "CREATE TRIGGER tape_submissions_guard BEFORE UPDATE ON tape_submissions "
        "FOR EACH ROW EXECUTE FUNCTION onepledge_tape_guard()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_chain_guard BEFORE INSERT ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION onepledge_audit_guard()"
    )


def downgrade() -> None:
    """Drop the triggers and the functions."""
    op.execute("DROP TRIGGER audit_log_chain_guard ON audit_log")
    op.execute("DROP TRIGGER tape_submissions_guard ON tape_submissions")
    op.execute("DROP TRIGGER loans_guard ON loans")
    for table in INSERT_ONLY_TABLES:
        op.execute(f"DROP TRIGGER {table}_insert_only ON {table}")
    for function in (
        "onepledge_audit_guard",
        "onepledge_tape_guard",
        "onepledge_loans_guard",
        "onepledge_forbid_mutation",
    ):
        op.execute(f"DROP FUNCTION {function}()")
