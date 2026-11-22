"""What may change on a guarded row, column by column (SPEC_DECISIONS 87).

Every column of `loans`, `tape_submissions` and `alerts` is classified here, and a test
fails when a table has a column that is not. The guards of migration 0010 are written by
hand from this table; a parametrised test changes each immutable and each conditionally
mutable column alone, for each role and each relevant row state, and expects the guard's
SQLSTATE, so a clause deleted from a guard fails a test.

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
    }
)

GUARD_CODES: Mapping[str, str] = MappingProxyType(
    {"loans": "OP003", "tape_submissions": "OP004", "alerts": "OP006"}
)
"""The SQLSTATE each table's guard raises for an immutable or conditional column."""

FROZEN_CODES: Mapping[str, str] = MappingProxyType(
    {"loans": "OP002", "tape_submissions": "OP004", "alerts": "OP006"}
)
"""The SQLSTATE for any change to a frozen row: a RELEASED loan, a COMPLETED tape, a closed alert."""

CONDITIONS: Mapping[str, str] = MappingProxyType(
    {
        "loans": "borrower, date, amount and loan number only while no loan_tokens row exists for the loan",
        "tape_submissions": "attempts and next_attempt_at only while the status is neither COMPLETED nor "
        "FAILED, or on the resume out of FAILED",
        "alerts": "bank_position and position_submitted_at from null to a value once, while REVIEWING; "
        "note, reviewed_by and reviewed_at while NEW or REVIEWING",
    }
)
