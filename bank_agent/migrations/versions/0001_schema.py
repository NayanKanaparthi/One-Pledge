"""The 18 tables of SPEC 12.1 with their checks, keys and indexes.

Every status or code column carries a named CHECK listing its values (the lists are
written out here, so a later change of the code never rewrites this revision). Every
hash column is checked to be 64 lower case hex characters. Foreign keys restrict
deletion. `tape_submissions` and `loans_raw` hold the verbatim forms of SPEC_DECISIONS 25
(BOM flag, header and trailer text, header cells; each record's text and cells), and a
raw row's hash is checked to be the SHA-256 of its text.

Revision ID: 0001_schema
Revises:
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_schema"
down_revision = None
branch_labels = None
depends_on = None

TAPE_KINDS = ("BACK_BOOK", "PLEDGE", "RELEASE")
TAPE_STATUSES = ("RECEIVED", "CLEANED", "CHECKED", "TOKENIZED", "SUBMITTED", "COMPLETED", "FAILED")
RESUMABLE = ("RECEIVED", "CLEANED", "CHECKED", "TOKENIZED", "SUBMITTED")
STAGES = ("CLEANED", "CHECKED", "TOKENIZED", "SUBMITTED", "COMPLETED")
FAILURE_CODES = (
    "OUTSIDE_RECORDS_UNAVAILABLE", "OUTSIDE_RECORDS_REJECTED", "KEY_HOLDER_UNAVAILABLE",
    "KEY_HOLDER_REJECTED", "KEY_VERSION_MISMATCH", "REGISTRY_UNAVAILABLE", "REGISTRY_REJECTED_REQUEST",
    "CERTIFICATE_INVALID", "REGISTRY_INTEGRITY_BROKEN", "INTERNAL_ERROR",
)  # fmt: skip
LOAN_STATES = ("PENDING", "HELD_LOCAL", "LOCKED", "CONFLICT", "RELEASED")
DISPOSITIONS = ("ACCEPTED_NEW", "ACCEPTED_REPRESENTED", "ALREADY_LOCKED", "RELEASE_REQUESTED", "REJECTED")
CHECK_TYPES = ("TITLE_EXISTS", "LIENHOLDER_MATCH", "PAYMENTS_RECEIVED", "CONTRACT_PRESENT")
CHECK_OUTCOMES = ("PASS", "FAIL", "UNKNOWN")
CHECK_REASONS = (
    "TITLE_FOUND", "TITLE_PENDING_WINDOW", "NO_TITLE_RECORD", "LIENHOLDER_MATCHES", "LIENHOLDER_MISMATCH",
    "FIRST_PAYMENT_NOT_DUE", "PAYMENT_IN_WINDOW", "NO_PAYMENT_IN_WINDOW", "NO_CONTRACT_RECORD",
    "CONTRACT_LENDER_MISMATCH", "CONTRACT_TERMS_MISMATCH", "CONTRACT_MATCHES",
)  # fmt: skip
CHECK_COMBINATIONS = (
    ("TITLE_EXISTS", "PASS", "TITLE_FOUND"),
    ("TITLE_EXISTS", "UNKNOWN", "TITLE_PENDING_WINDOW"),
    ("TITLE_EXISTS", "FAIL", "NO_TITLE_RECORD"),
    ("LIENHOLDER_MATCH", "PASS", "LIENHOLDER_MATCHES"),
    ("LIENHOLDER_MATCH", "FAIL", "LIENHOLDER_MISMATCH"),
    ("LIENHOLDER_MATCH", "UNKNOWN", "TITLE_PENDING_WINDOW"),
    ("LIENHOLDER_MATCH", "UNKNOWN", "NO_TITLE_RECORD"),
    ("PAYMENTS_RECEIVED", "UNKNOWN", "FIRST_PAYMENT_NOT_DUE"),
    ("PAYMENTS_RECEIVED", "PASS", "PAYMENT_IN_WINDOW"),
    ("PAYMENTS_RECEIVED", "FAIL", "NO_PAYMENT_IN_WINDOW"),
    ("CONTRACT_PRESENT", "FAIL", "NO_CONTRACT_RECORD"),
    ("CONTRACT_PRESENT", "FAIL", "CONTRACT_LENDER_MISMATCH"),
    ("CONTRACT_PRESENT", "FAIL", "CONTRACT_TERMS_MISMATCH"),
    ("CONTRACT_PRESENT", "PASS", "CONTRACT_MATCHES"),
)
CLEANING_PROBLEMS = (
    "WHITESPACE_TRIMMED", "MISSING_REQUIRED_FIELD", "NON_DATA_ROW", "COLUMN_COUNT_MISMATCH",
    "UNSAFE_CHARACTERS", "VIN_CASE_NORMALISED", "VIN_SEPARATORS_REMOVED", "VIN_CHARACTER_SUBSTITUTED",
    "VIN_LENGTH_INVALID", "VIN_CHARACTER_INVALID", "VIN_CHECK_DIGIT_INVALID", "BORROWER_REF_NORMALISED",
    "BORROWER_REF_INVALID", "LOAN_NUMBER_INVALID", "DATE_FORMAT_NORMALISED", "DATE_UNPARSEABLE",
    "DATE_IN_FUTURE", "DATE_TOO_OLD", "AMOUNT_FORMAT_NORMALISED", "AMOUNT_UNPARSEABLE", "AMOUNT_OUT_OF_RANGE",
    "ZERO_BALANCE", "BALANCE_EXCEEDS_ORIGINAL", "DUPLICATE_VIN_IN_FILE", "TERMS_CHANGED_AFTER_TOKENIZATION",
    "TERMS_CHANGED_WHILE_LOCKED", "OPEN_CONFLICT", "RELEASE_LOAN_NOT_LOCKED",
)  # fmt: skip
CLEANING_FIELDS = (
    "row", "lender_loan_number", "vin", "borrower_ref", "origination_date", "original_amount",
    "current_balance",
)  # fmt: skip
TOKEN_KINDS = (
    "ASSET",
    "VIN",
    "BORROWER",
    "AMOUNT_EXACT",
    "AMOUNT_100",
    "AMOUNT_1000",
    "DATE_DAY",
    "DATE_MONTH",
)
CASE_STATUSES = ("OPEN", "CLEARED_DATA_ERROR", "CLEARED_LATE_RELEASE", "CONFIRMED")
AUDIT_ACTIONS = (
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
AUDIT_OBJECT_TYPES = (
    "USER", "ROUTE", "TAPE_SUBMISSION", "LOAN", "REGISTRY_REQUEST", "CERTIFICATE", "ALERT", "CHECKPOINT",
    "REGISTRY_MESSAGE", "REGISTRY", "INTEGRITY_RUN", "AUDIT_LOG", "CONFIG", "REFERENCE_DATA", "SCHEMA",
)  # fmt: skip
HEX64 = "'^[0-9a-f]{64}$'"
KEY_VERSION = "'^[1-9][0-9]*\\.[1-9][0-9]*$'"


def _uuid(
    name: str, *args: sa.ForeignKey, nullable: bool = False, primary_key: bool = False
) -> sa.Column[uuid.UUID]:
    return sa.Column(name, postgresql.UUID(as_uuid=True), *args, nullable=nullable, primary_key=primary_key)


def _fk(target: str) -> sa.ForeignKey:
    return sa.ForeignKey(target, ondelete="RESTRICT")


def _time(name: str, *, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _text(name: str, *, nullable: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.Text(), nullable=nullable)


def _hash(name: str) -> sa.Column[str]:
    return sa.Column(name, sa.CHAR(64), nullable=False)


def _in(table: str, column: str, values: Sequence[str], *, nullable: bool = False) -> sa.CheckConstraint:
    listed = ", ".join(f"'{value}'" for value in values)
    condition = f"{column} IN ({listed})"
    if nullable:
        condition = f"{column} IS NULL OR {condition}"
    return sa.CheckConstraint(condition, name=f"ck_{table}_{column}")


def _hex(table: str, column: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{column} ~ {HEX64}", name=f"ck_{table}_{column}")


def _kinds(table: str, column: str) -> sa.CheckConstraint:
    listed = ", ".join(f"'{value}'" for value in TOKEN_KINDS)
    return sa.CheckConstraint(f"{column} <@ ARRAY[{listed}]::text[]", name=f"ck_{table}_{column}")


def _array(table: str, column: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"jsonb_typeof({column}) = 'array'", name=f"ck_{table}_{column}")


def _object(table: str, column: str, *, nullable: bool = False) -> sa.CheckConstraint:
    condition = f"jsonb_typeof({column}) = 'object'"
    if nullable:
        condition = f"{column} IS NULL OR {condition}"
    return sa.CheckConstraint(condition, name=f"ck_{table}_{column}")


def _lenders() -> None:
    op.create_table(
        "lenders",
        _uuid("lender_id", primary_key=True),
        sa.Column("legal_name", sa.Text(), nullable=False, unique=True),
        sa.Column("lei", sa.CHAR(20), nullable=False, unique=True),
        _time("created_at"),
        sa.CheckConstraint("lei ~ '^[A-Z0-9]{18}[0-9]{2}$'", name="ck_lenders_lei"),
    )


def _facilities() -> None:
    op.create_table(
        "facilities",
        _uuid("facility_id", primary_key=True),
        _uuid("lender_id", _fk("lenders.lender_id")),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        _text("name"),
        sa.Column("credit_limit_cents", sa.BigInteger(), nullable=False),
        sa.Column("advance_rate_bps", sa.Integer(), nullable=False),
        _text("status"),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.CheckConstraint("code ~ '^[A-Z0-9-]{3,40}$'", name="ck_facilities_code"),
        sa.CheckConstraint("credit_limit_cents >= 0", name="ck_facilities_credit_limit_cents"),
        sa.CheckConstraint("advance_rate_bps BETWEEN 0 AND 10000", name="ck_facilities_advance_rate_bps"),
        _in("facilities", "status", ("ACTIVE", "SUSPENDED", "CLOSED")),
    )


def _users() -> None:
    op.create_table(
        "users",
        _uuid("user_id", primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        _text("display_name"),
        _text("role"),
        _text("password_hash"),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _time("created_at"),
        _time("updated_at"),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email"),
        sa.CheckConstraint("token_version >= 0", name="ck_users_token_version"),
        _in("users", "role", ("ANALYST", "CREDIT_OFFICER", "ADMIN")),
    )


def _login_attempts() -> None:
    op.create_table(
        "login_attempts",
        sa.Column("attempt_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        _hash("email_sha256"),
        sa.Column("client_addr", postgresql.INET(), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        _time("at"),
        _hex("login_attempts", "email_sha256"),
    )
    op.create_index("ix_login_attempts_email_sha256_at", "login_attempts", ["email_sha256", "at"])
    op.create_index("ix_login_attempts_client_addr_at", "login_attempts", ["client_addr", "at"])


def _tape_submissions() -> None:
    op.create_table(
        "tape_submissions",
        _uuid("submission_id", primary_key=True),
        _uuid("facility_id", _fk("facilities.facility_id")),
        _text("kind"),
        _text("source"),
        _text("file_name"),
        _hash("file_sha256"),
        _hash("rows_sha256"),
        sa.Column("has_bom", sa.Boolean(), nullable=False),
        _text("header_text"),
        sa.Column("header_cells", postgresql.JSONB(), nullable=False),
        _text("trailer_text"),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        _text("status"),
        _text("resume_status", nullable=True),
        _text("failed_stage", nullable=True),
        _text("failure_code", nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _time("next_attempt_at", nullable=True),
        _uuid("uploaded_by", _fk("users.user_id")),
        _time("uploaded_at"),
        _time("updated_at"),
        _time("completed_at", nullable=True),
        sa.UniqueConstraint("facility_id", "kind", "file_sha256", name="uq_tape_submissions_file"),
        _in("tape_submissions", "kind", TAPE_KINDS),
        _in("tape_submissions", "source", ("FILE_UPLOAD", "API_INSTRUCTION")),
        _in("tape_submissions", "status", TAPE_STATUSES),
        _in("tape_submissions", "resume_status", RESUMABLE, nullable=True),
        _in("tape_submissions", "failed_stage", STAGES, nullable=True),
        _in("tape_submissions", "failure_code", FAILURE_CODES, nullable=True),
        _hex("tape_submissions", "file_sha256"),
        _hex("tape_submissions", "rows_sha256"),
        sa.CheckConstraint("row_count >= 1", name="ck_tape_submissions_row_count"),
        _array("tape_submissions", "header_cells"),
        sa.CheckConstraint("attempts >= 0", name="ck_tape_submissions_attempts"),
        sa.CheckConstraint(
            "(status = 'FAILED') = (failure_code IS NOT NULL)", name="ck_tape_submissions_failed_has_code"
        ),
        sa.CheckConstraint(
            "(status = 'FAILED') = (resume_status IS NOT NULL)", name="ck_tape_submissions_failed_has_resume"
        ),
        sa.CheckConstraint(
            "(status = 'FAILED') = (failed_stage IS NOT NULL)", name="ck_tape_submissions_failed_has_stage"
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)", name="ck_tape_submissions_completed_at"
        ),
    )
    op.create_index("ix_tape_submissions_status_uploaded_at", "tape_submissions", ["status", "uploaded_at"])
    op.create_index(
        "ix_tape_submissions_facility_uploaded_at", "tape_submissions", ["facility_id", "uploaded_at"]
    )


def _loans_raw() -> None:
    op.create_table(
        "loans_raw",
        _uuid("raw_id", primary_key=True),
        _uuid("submission_id", _fk("tape_submissions.submission_id")),
        sa.Column("row_number", sa.Integer(), nullable=False),
        _text("raw_text"),
        sa.Column("raw_cells", postgresql.JSONB(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        _hash("row_sha256"),
        _time("received_at"),
        sa.UniqueConstraint("submission_id", "row_number", name="uq_loans_raw_submission_row"),
        sa.CheckConstraint("row_number >= 1", name="ck_loans_raw_row_number"),
        _hex("loans_raw", "row_sha256"),
        sa.CheckConstraint(
            "row_sha256 = encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')",
            name="ck_loans_raw_row_sha256_text",
        ),
        _array("loans_raw", "raw_cells"),
        _object("loans_raw", "raw_payload"),
    )


def _cleaning_issues() -> None:
    op.create_table(
        "cleaning_issues",
        _uuid("issue_id", primary_key=True),
        _uuid("raw_id", _fk("loans_raw.raw_id")),
        _text("field"),
        _text("problem"),
        _text("action"),
        sa.UniqueConstraint("raw_id", "field", "problem", name="uq_cleaning_issues_raw_field_problem"),
        _in("cleaning_issues", "field", CLEANING_FIELDS),
        _in("cleaning_issues", "problem", CLEANING_PROBLEMS),
        _in("cleaning_issues", "action", ("FIXED", "REJECTED")),
    )


def _loans() -> None:
    op.create_table(
        "loans",
        _uuid("loan_id", primary_key=True),
        _uuid("facility_id", _fk("facilities.facility_id")),
        _uuid("first_submission_id", _fk("tape_submissions.submission_id")),
        _text("lender_loan_number"),
        sa.Column("vin", sa.CHAR(17), nullable=False),
        _text("borrower_ref"),
        sa.Column("origination_date", sa.Date(), nullable=False),
        sa.Column("original_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("current_balance_cents", sa.BigInteger(), nullable=False),
        _text("state"),
        _time("created_at"),
        _time("updated_at"),
        sa.CheckConstraint("vin ~ '^[A-HJ-NPR-Z0-9]{17}$'", name="ck_loans_vin"),
        sa.CheckConstraint("borrower_ref ~ '^[A-Z0-9]{4,32}$'", name="ck_loans_borrower_ref"),
        sa.CheckConstraint(
            "lender_loan_number ~ '^[A-Za-z0-9][A-Za-z0-9_/-]{0,39}$'", name="ck_loans_lender_loan_number"
        ),
        sa.CheckConstraint("original_amount_cents > 0", name="ck_loans_original_amount_cents"),
        sa.CheckConstraint(
            "current_balance_cents > 0 AND current_balance_cents <= original_amount_cents",
            name="ck_loans_current_balance_cents",
        ),
        _in("loans", "state", LOAN_STATES),
    )
    op.create_index(
        "uq_loans_facility_vin_live",
        "loans",
        ["facility_id", "vin"],
        unique=True,
        postgresql_where=sa.text("state <> 'RELEASED'"),
    )
    op.create_index("ix_loans_facility_state", "loans", ["facility_id", "state"])
    op.create_index("ix_loans_vin", "loans", ["vin"])


def _submission_items() -> None:
    op.create_table(
        "submission_items",
        _uuid("item_id", primary_key=True),
        _uuid("submission_id", _fk("tape_submissions.submission_id")),
        _uuid("raw_id", _fk("loans_raw.raw_id")),
        sa.Column("row_number", sa.Integer(), nullable=False),
        _text("disposition"),
        _uuid("loan_id", _fk("loans.loan_id"), nullable=True),
        sa.UniqueConstraint("raw_id", name="uq_submission_items_raw_id"),
        sa.UniqueConstraint("submission_id", "row_number", name="uq_submission_items_submission_row"),
        sa.UniqueConstraint("submission_id", "loan_id", name="uq_submission_items_submission_loan"),
        sa.CheckConstraint("row_number >= 1", name="ck_submission_items_row_number"),
        _in("submission_items", "disposition", DISPOSITIONS),
        sa.CheckConstraint(
            "(disposition = 'REJECTED') = (loan_id IS NULL)", name="ck_submission_items_rejected_has_no_loan"
        ),
    )
    op.create_index("ix_submission_items_loan_id", "submission_items", ["loan_id"])


def _local_checks() -> None:
    combinations = " OR ".join(
        f"(check_type = '{c}' AND result = '{o}' AND reason_code = '{r}')" for c, o, r in CHECK_COMBINATIONS
    )
    op.create_table(
        "local_checks",
        _uuid("check_id", primary_key=True),
        _uuid("loan_id", _fk("loans.loan_id")),
        _uuid("submission_id", _fk("tape_submissions.submission_id")),
        _text("check_type"),
        _text("result"),
        _text("reason_code"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        _time("checked_at"),
        sa.UniqueConstraint(
            "loan_id", "submission_id", "check_type", name="uq_local_checks_loan_submission_type"
        ),
        _in("local_checks", "check_type", CHECK_TYPES),
        _in("local_checks", "result", CHECK_OUTCOMES),
        _in("local_checks", "reason_code", CHECK_REASONS),
        sa.CheckConstraint(combinations, name="ck_local_checks_combination"),
        _object("local_checks", "evidence"),
    )


def _loan_tokens() -> None:
    op.create_table(
        "loan_tokens",
        _uuid("loan_id", _fk("loans.loan_id")),
        _text("token_kind"),
        _text("key_version"),
        _hash("token"),
        _time("created_at"),
        sa.PrimaryKeyConstraint("loan_id", "token_kind", "key_version", name="pk_loan_tokens"),
        _in("loan_tokens", "token_kind", TOKEN_KINDS),
        sa.CheckConstraint(f"key_version ~ {KEY_VERSION}", name="ck_loan_tokens_key_version"),
        _hex("loan_tokens", "token"),
    )
    op.create_index("ix_loan_tokens_kind_token", "loan_tokens", ["token_kind", "token"])


def _registry_requests() -> None:
    op.create_table(
        "registry_requests",
        _uuid("request_id", primary_key=True),
        _uuid("submission_id", _fk("tape_submissions.submission_id")),
        _text("origin"),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.CHAR(64), nullable=False, unique=True),
        _text("kind"),
        _text("key_version"),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        _time("sent_at", nullable=True),
        _time("last_attempt_at", nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        _time("responded_at", nullable=True),
        _uuid("registry_submission_id", nullable=True),
        sa.Column("as_of_seq", sa.BigInteger(), nullable=True),
        _uuid("certificate_id", nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        _in("registry_requests", "origin", ("TAPE", "RESUBMIT")),
        _in("registry_requests", "kind", TAPE_KINDS),
        _hex("registry_requests", "idempotency_key"),
        sa.CheckConstraint(f"key_version ~ {KEY_VERSION}", name="ck_registry_requests_key_version"),
        sa.CheckConstraint("item_count BETWEEN 1 AND 500", name="ck_registry_requests_item_count"),
        sa.CheckConstraint("batch_index >= 0", name="ck_registry_requests_batch_index"),
        sa.CheckConstraint("attempts >= 0", name="ck_registry_requests_attempts"),
        _object("registry_requests", "response_body", nullable=True),
    )
    op.create_index(
        "uq_registry_requests_tape_batch",
        "registry_requests",
        ["submission_id", "batch_index"],
        unique=True,
        postgresql_where=sa.text("origin = 'TAPE'"),
    )


def _registry_request_items() -> None:
    op.create_table(
        "registry_request_items",
        _uuid("request_id", _fk("registry_requests.request_id")),
        _uuid("item_ref"),
        sa.Column("position", sa.Integer(), nullable=False),
        _uuid("loan_id", _fk("loans.loan_id")),
        _text("result", nullable=True),
        sa.Column("already_held", sa.Boolean(), nullable=True),
        _text("match_kind", nullable=True),
        _text("match_basis", nullable=True),
        sa.Column(
            "kinds_matched",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        _uuid("registry_case_id", nullable=True),
        _uuid("lock_id", nullable=True),
        _text("reason", nullable=True),
        sa.PrimaryKeyConstraint("request_id", "item_ref", name="pk_registry_request_items"),
        sa.UniqueConstraint("request_id", "loan_id", name="uq_registry_request_items_request_loan"),
        sa.UniqueConstraint("request_id", "position", name="uq_registry_request_items_request_position"),
        sa.CheckConstraint("position >= 0", name="ck_registry_request_items_position"),
        _in(
            "registry_request_items", "result", ("LOCKED", "CONFLICT", "RELEASED", "REJECTED"), nullable=True
        ),
        _in("registry_request_items", "match_kind", ("EXACT", "LIKELY"), nullable=True),
        _in("registry_request_items", "match_basis", ("ASSET", "FIELDS"), nullable=True),
        _kinds("registry_request_items", "kinds_matched"),
    )
    op.create_index("ix_registry_request_items_loan_id", "registry_request_items", ["loan_id"])


def _certificates() -> None:
    op.create_table(
        "certificates",
        _uuid("certificate_id", primary_key=True),
        _uuid("request_id", _fk("registry_requests.request_id")),
        _uuid("registry_submission_id"),
        _text("kind"),
        sa.Column("as_of_seq", sa.BigInteger(), nullable=False),
        _time("issued_at"),
        sa.Column("body", postgresql.JSONB(), nullable=False),
        _hash("body_hash"),
        _text("signature"),
        _text("signing_key_id"),
        sa.Column("verified", sa.Boolean(), nullable=False),
        _time("verified_at"),
        _text("failure_reason", nullable=True),
        _time("received_at"),
        sa.UniqueConstraint("request_id", name="uq_certificates_request_id"),
        _in("certificates", "kind", TAPE_KINDS),
        _hex("certificates", "body_hash"),
        sa.CheckConstraint("as_of_seq >= 1", name="ck_certificates_as_of_seq"),
        _in(
            "certificates",
            "failure_reason",
            ("BAD_SIGNATURE", "UNKNOWN_SIGNING_KEY", "BODY_HASH_MISMATCH", "CONTENT_MISMATCH"),
            nullable=True,
        ),
        sa.CheckConstraint("verified = (failure_reason IS NULL)", name="ck_certificates_verified"),
        _object("certificates", "body"),
    )


def _checkpoints_seen() -> None:
    op.create_table(
        "checkpoints_seen",
        _uuid("checkpoint_id", primary_key=True),
        sa.Column("checkpoint_seq", sa.BigInteger(), nullable=False),
        sa.Column("last_event_seq", sa.BigInteger(), nullable=False),
        _hash("head_hash"),
        _time("signed_at"),
        _text("signature"),
        _text("signing_key_id"),
        _time("received_at"),
        sa.Column("verified", sa.Boolean(), nullable=False),
        _time("verified_at"),
        _text("failure_reason", nullable=True),
        sa.Column("first_bad_event_seq", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("checkpoint_seq", "head_hash", "signature", name="uq_checkpoints_seen_delivery"),
        sa.CheckConstraint("checkpoint_seq >= 1", name="ck_checkpoints_seen_checkpoint_seq"),
        sa.CheckConstraint("last_event_seq >= 0", name="ck_checkpoints_seen_last_event_seq"),
        _hex("checkpoints_seen", "head_hash"),
        _in(
            "checkpoints_seen",
            "failure_reason",
            ("BAD_SIGNATURE", "UNKNOWN_SIGNING_KEY", "NON_MONOTONIC", "FORK", "CHAIN_BREAK", "HEAD_MISMATCH"),
            nullable=True,
        ),
        sa.CheckConstraint("verified = (failure_reason IS NULL)", name="ck_checkpoints_seen_verified"),
    )
    op.create_index(
        "uq_checkpoints_seen_verified_seq",
        "checkpoints_seen",
        ["checkpoint_seq"],
        unique=True,
        postgresql_where=sa.text("verified"),
    )


def _alerts() -> None:
    op.create_table(
        "alerts",
        _uuid("alert_id", primary_key=True),
        _uuid("registry_case_id"),
        _uuid("loan_id", _fk("loans.loan_id")),
        _text("role"),
        _text("match_kind"),
        _text("match_basis"),
        sa.Column("kinds_matched", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("kinds_not_matched", postgresql.ARRAY(sa.Text()), nullable=False),
        _text("explanation_text"),
        _text("explanation_source"),
        _text("status"),
        _text("case_status"),
        _text("bank_position", nullable=True),
        _time("position_submitted_at", nullable=True),
        _text("note", nullable=True),
        _time("received_at"),
        _uuid("reviewed_by", _fk("users.user_id"), nullable=True),
        _time("reviewed_at", nullable=True),
        sa.UniqueConstraint("registry_case_id", "loan_id", name="uq_alerts_case_loan"),
        _in("alerts", "role", ("HOLDER", "CLAIMANT")),
        _in("alerts", "match_kind", ("EXACT", "LIKELY")),
        _in("alerts", "match_basis", ("ASSET", "FIELDS")),
        _in("alerts", "explanation_source", ("TEMPLATE", "MODEL")),
        _in("alerts", "status", ("NEW", "REVIEWING", "CLEARED", "ESCALATED")),
        _in("alerts", "case_status", CASE_STATUSES),
        _in("alerts", "bank_position", ("DATA_ERROR", "CONFIRMED_DOUBLE_PLEDGE"), nullable=True),
        sa.CheckConstraint("note IS NULL OR length(note) <= 500", name="ck_alerts_note"),
        _kinds("alerts", "kinds_matched"),
        _kinds("alerts", "kinds_not_matched"),
    )
    op.create_index("ix_alerts_status_received_at", "alerts", ["status", "received_at"])
    op.create_index("ix_alerts_loan_id", "alerts", ["loan_id"])


def _audit_log() -> None:
    op.create_table(
        "audit_log",
        _uuid("audit_id", primary_key=True),
        sa.Column("seq", sa.BigInteger(), nullable=False, unique=True),
        _time("at"),
        _text("actor_type"),
        _uuid("actor_user_id", _fk("users.user_id"), nullable=True),
        _text("action"),
        _text("object_type"),
        _uuid("object_id", nullable=True),
        _text("outcome"),
        _text("request_id", nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        _hash("prev_hash"),
        sa.Column("entry_hash", sa.CHAR(64), nullable=False, unique=True),
        sa.CheckConstraint("seq >= 1", name="ck_audit_log_seq"),
        _in("audit_log", "actor_type", ("USER", "SYSTEM")),
        sa.CheckConstraint(
            "(actor_type = 'USER') = (actor_user_id IS NOT NULL)", name="ck_audit_log_actor_user"
        ),
        _in("audit_log", "action", AUDIT_ACTIONS),
        _in("audit_log", "object_type", AUDIT_OBJECT_TYPES),
        _in("audit_log", "outcome", ("SUCCESS", "DENIED", "FAILURE")),
        sa.CheckConstraint("request_id IS NULL OR length(request_id) <= 64", name="ck_audit_log_request_id"),
        _hex("audit_log", "prev_hash"),
        _hex("audit_log", "entry_hash"),
        _object("audit_log", "detail"),
    )


def _integrity_runs() -> None:
    op.create_table(
        "integrity_runs",
        _uuid("run_id", primary_key=True),
        _time("ran_at"),
        _uuid("triggered_by", _fk("users.user_id"), nullable=True),
        _text("status"),
        sa.Column("report", postgresql.JSONB(), nullable=False),
        _in("integrity_runs", "status", ("OK", "BROKEN")),
        _object("integrity_runs", "report"),
    )


def upgrade() -> None:
    """Create every table of SPEC 12.1, each after the tables it references."""
    _lenders()
    _facilities()
    _users()
    _login_attempts()
    _tape_submissions()
    _loans_raw()
    _cleaning_issues()
    _loans()
    _submission_items()
    _local_checks()
    _loan_tokens()
    _registry_requests()
    _registry_request_items()
    _certificates()
    _checkpoints_seen()
    _alerts()
    _audit_log()
    _integrity_runs()


def downgrade() -> None:
    """Drop every table, dependants first."""
    for table in (
        "integrity_runs", "audit_log", "alerts", "checkpoints_seen", "certificates", "registry_request_items",
        "registry_requests", "loan_tokens", "local_checks", "submission_items", "loans", "cleaning_issues",
        "loans_raw", "tape_submissions", "login_attempts", "users", "facilities", "lenders",
    ):  # fmt: skip
        op.drop_table(table)
