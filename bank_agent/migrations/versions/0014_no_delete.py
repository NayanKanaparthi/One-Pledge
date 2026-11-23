"""No row is ever deleted, and an answered registry request is frozen (SPEC_DECISIONS 106).

1. `onepledge_forbid_delete()`: a statement level trigger `{table}_no_delete` before DELETE and
   TRUNCATE on each of the eleven tables that has no insert-only trigger (`lenders`,
   `facilities`, `users`, `login_attempts`, `tape_submissions`, `loans`, `registry_requests`,
   `registry_request_items`, `alerts`, `registry_outbox_messages`, `registry_outbox_state`)
   refuses both for everyone, with SQLSTATE `OP008`. Users are deactivated, never removed.
2. `onepledge_requests_guard()` (`OP007`): the identity of a registry request never changes;
   `attempts`, `sent_at` and `last_attempt_at` change until it is answered; the answer columns
   are set once, together with `response_status`; an answered request never changes.
3. `onepledge_request_items_guard()` (`OP007`): the identity of an item never changes; its
   result columns are set once, together with `result`; an item with a result never changes.

The column classes are `infrastructure/db/guard_columns.py`.

Revision ID: 0014_no_delete
Revises: 0013_outbox_ledger
"""

from __future__ import annotations

from alembic import op

revision = "0014_no_delete"
down_revision = "0013_outbox_ledger"
branch_labels = None
depends_on = None

NO_DELETE_TABLES = (
    "lenders", "facilities", "users", "login_attempts", "tape_submissions", "loans", "registry_requests",
    "registry_request_items", "alerts", "registry_outbox_messages", "registry_outbox_state",
)  # fmt: skip

FORBID_DELETE = """
CREATE FUNCTION onepledge_forbid_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'rows of % are never deleted', TG_TABLE_NAME USING ERRCODE = 'OP008';
END;
$$
"""

REQUESTS_GUARD = """
CREATE FUNCTION onepledge_requests_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.response_status IS NOT NULL AND ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) THEN
        RAISE EXCEPTION 'an answered registry request never changes' USING ERRCODE = 'OP007';
    END IF;
    IF NEW.request_id IS DISTINCT FROM OLD.request_id
       OR NEW.submission_id IS DISTINCT FROM OLD.submission_id
       OR NEW.origin IS DISTINCT FROM OLD.origin
       OR NEW.batch_index IS DISTINCT FROM OLD.batch_index
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.key_version IS DISTINCT FROM OLD.key_version
       OR NEW.item_count IS DISTINCT FROM OLD.item_count THEN
        RAISE EXCEPTION 'the identity of a registry request is immutable' USING ERRCODE = 'OP007';
    END IF;
    IF NEW.response_status IS NULL AND ROW(
           NEW.responded_at, NEW.registry_submission_id, NEW.as_of_seq, NEW.certificate_id,
           NEW.response_body
       ) IS DISTINCT FROM ROW(
           OLD.responded_at, OLD.registry_submission_id, OLD.as_of_seq, OLD.certificate_id,
           OLD.response_body
       ) THEN
        RAISE EXCEPTION 'an answer is stored with its status' USING ERRCODE = 'OP007';
    END IF;
    RETURN NEW;
END;
$$
"""

ITEMS_GUARD = """
CREATE FUNCTION onepledge_request_items_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.result IS NOT NULL AND ROW(NEW.*) IS DISTINCT FROM ROW(OLD.*) THEN
        RAISE EXCEPTION 'an item with a result never changes' USING ERRCODE = 'OP007';
    END IF;
    IF NEW.request_id IS DISTINCT FROM OLD.request_id
       OR NEW.item_ref IS DISTINCT FROM OLD.item_ref
       OR NEW.position IS DISTINCT FROM OLD.position
       OR NEW.loan_id IS DISTINCT FROM OLD.loan_id THEN
        RAISE EXCEPTION 'the identity of a request item is immutable' USING ERRCODE = 'OP007';
    END IF;
    IF NEW.result IS NULL AND ROW(
           NEW.already_held, NEW.match_kind, NEW.match_basis, NEW.kinds_matched, NEW.registry_case_id,
           NEW.lock_id, NEW.reason
       ) IS DISTINCT FROM ROW(
           OLD.already_held, OLD.match_kind, OLD.match_basis, OLD.kinds_matched, OLD.registry_case_id,
           OLD.lock_id, OLD.reason
       ) THEN
        RAISE EXCEPTION 'a result is stored with its outcome' USING ERRCODE = 'OP007';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    """The no-delete triggers and the two request guards."""
    op.execute(FORBID_DELETE)
    for table in NO_DELETE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE OR TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION onepledge_forbid_delete()"
        )
    op.execute(REQUESTS_GUARD)
    op.execute(
        "CREATE TRIGGER registry_requests_guard BEFORE UPDATE ON registry_requests "
        "FOR EACH ROW EXECUTE FUNCTION onepledge_requests_guard()"
    )
    op.execute(ITEMS_GUARD)
    op.execute(
        "CREATE TRIGGER registry_request_items_guard BEFORE UPDATE ON registry_request_items "
        "FOR EACH ROW EXECUTE FUNCTION onepledge_request_items_guard()"
    )


def downgrade() -> None:
    """Back to 0013."""
    op.execute("DROP TRIGGER registry_request_items_guard ON registry_request_items")
    op.execute("DROP FUNCTION onepledge_request_items_guard()")
    op.execute("DROP TRIGGER registry_requests_guard ON registry_requests")
    op.execute("DROP FUNCTION onepledge_requests_guard()")
    for table in NO_DELETE_TABLES:
        op.execute(f"DROP TRIGGER {table}_no_delete ON {table}")
    op.execute("DROP FUNCTION onepledge_forbid_delete()")
