"""What may change on a guarded row, column by column (SPEC_DECISIONS 87).

Every column of `loans`, `tape_submissions`, `alerts`, `registry_requests` and
`registry_request_items` is classified here, and a test fails when a table has a column that is
not. The guards of migrations 0010 and 0014 (the two request tables, SPEC_DECISIONS 106) are
written by hand from this table; a parametrised test changes each immutable and each
conditionally mutable column alone, for each role and each relevant row state, and expects the
guard's SQLSTATE, so a clause deleted from a guard fails a test.

| Class | Meaning |
|---|---|
| `IMMUTABLE` | never changes after insert |
| `CONDITIONAL` | changes only under the stated condition (`CONDITIONS`) |
| `MUTABLE` | changes through the state machine of SPEC 8.3, 8.4 or 8.5 only |
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class ColumnClass(StrEnum):
    """The three classes of decision 87."""

    IMMUTABLE = "IMMUTABLE"
    CONDITIONAL = "CONDITIONAL"
    MUTABLE = "MUTABLE"


def _classes(immutable: str, conditional: str, mutable: str) -> Mapping[str, ColumnClass]:
    """Column names (space separated) by class."""
    return MappingProxyType(
        {
            **dict.fromkeys(immutable.split(), ColumnClass.IMMUTABLE),
            **dict.fromkeys(conditional.split(), ColumnClass.CONDITIONAL),
            **dict.fromkeys(mutable.split(), ColumnClass.MUTABLE),
        }
    )


GUARDED_COLUMNS: Mapping[str, Mapping[str, ColumnClass]] = MappingProxyType(
    {
        "loans": _classes(
            "loan_id facility_id vin first_submission_id created_at",
            "borrower_ref origination_date original_amount_cents lender_loan_number",
            "current_balance_cents state updated_at",
        ),
        "tape_submissions": _classes(
            "submission_id facility_id kind source file_name file_sha256 rows_sha256 has_bom header_text "
            "header_cells trailer_text as_of_date row_count uploaded_by uploaded_at",
            "attempts next_attempt_at",
            "status resume_status failed_stage failure_code updated_at completed_at",
        ),
        "alerts": _classes(
            "alert_id registry_case_id loan_id role match_kind match_basis kinds_matched kinds_not_matched "
            "received_at explanation_text explanation_source",
            "bank_position position_submitted_at note reviewed_by reviewed_at",
            "status case_status",
        ),
        "registry_requests": _classes(
            "request_id submission_id origin batch_index idempotency_key kind key_version item_count",
            "attempts sent_at last_attempt_at response_status responded_at registry_submission_id as_of_seq "
            "certificate_id response_body",
            "",
        ),
        "registry_request_items": _classes(
            "request_id item_ref position loan_id",
            "result already_held match_kind match_basis kinds_matched registry_case_id lock_id reason",
            "",
        ),
    }
)

GUARD_CODES: Mapping[str, str] = MappingProxyType(
    {
        "loans": "OP003", "tape_submissions": "OP004", "alerts": "OP006",
        "registry_requests": "OP007", "registry_request_items": "OP007",
    }
)  # fmt: skip
"""The SQLSTATE each table's guard raises for an immutable or conditional column."""

FROZEN_CODES: Mapping[str, str] = MappingProxyType(
    {
        "loans": "OP002", "tape_submissions": "OP004", "alerts": "OP006",
        "registry_requests": "OP007", "registry_request_items": "OP007",
    }
)  # fmt: skip
"""The SQLSTATE for any change to a frozen row: a RELEASED loan, a COMPLETED tape, a closed alert, an
answered registry request, an item with a result (SPEC_DECISIONS 106)."""

CONDITIONS: Mapping[str, str] = MappingProxyType(
    {
        "loans": "borrower, date, amount and loan number only while no loan_tokens row exists for the loan",
        "tape_submissions": "attempts and next_attempt_at only while the status is neither COMPLETED nor "
        "FAILED, or on the resume out of FAILED",
        "alerts": "bank_position and position_submitted_at from null to a value once, while REVIEWING; "
        "note, reviewed_by and reviewed_at while NEW or REVIEWING",
        "registry_requests": "attempts, sent_at and last_attempt_at until answered; the answer columns "
        "once, in the update that sets response_status",
        "registry_request_items": "the result columns once, in the update that sets result",
    }
)
