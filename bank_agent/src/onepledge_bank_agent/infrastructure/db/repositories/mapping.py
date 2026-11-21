"""Conversions between table rows and domain objects."""

from __future__ import annotations

from onepledge_bank_agent.domain.entities import Facility, FacilityWithLender, Lender
from onepledge_bank_agent.domain.enums import (
    FacilityStatus,
    FailureCode,
    LoanState,
    TapeKind,
    TapeSource,
    TapeStatus,
)
from onepledge_bank_agent.domain.loan import Loan, LoanRecord
from onepledge_bank_agent.domain.tape import TapeFacts, TapeProgress, TapeSubmission
from onepledge_bank_agent.domain.values import BorrowerRef, Lei, LenderLoanNumber, LoanTerms, Money
from onepledge_bank_agent.domain.vin import Vin
from onepledge_bank_agent.infrastructure.db.models import FacilityRow, LenderRow, LoanRow, TapeSubmissionRow


def tape_from_row(row: TapeSubmissionRow) -> TapeSubmission:
    """A stored submission as the entity."""
    facts = TapeFacts(
        submission_id=row.submission_id,
        facility_id=row.facility_id,
        kind=TapeKind(row.kind),
        source=TapeSource(row.source),
        file_name=row.file_name,
        file_sha256=row.file_sha256,
        rows_sha256=row.rows_sha256,
        as_of_date=row.as_of_date,
        row_count=row.row_count,
        uploaded_by=row.uploaded_by,
        uploaded_at=row.uploaded_at,
    )
    progress = TapeProgress(
        status=TapeStatus(row.status),
        resume_status=TapeStatus(row.resume_status) if row.resume_status else None,
        failed_stage=TapeStatus(row.failed_stage) if row.failed_stage else None,
        failure_code=FailureCode(row.failure_code) if row.failure_code else None,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )
    return TapeSubmission(facts, progress)


def tape_progress_values(tape: TapeSubmission) -> dict[str, object]:
    """The mutable columns of a submission."""
    progress = tape.progress
    return {
        "status": progress.status.value,
        "resume_status": progress.resume_status.value if progress.resume_status else None,
        "failed_stage": progress.failed_stage.value if progress.failed_stage else None,
        "failure_code": progress.failure_code.value if progress.failure_code else None,
        "attempts": progress.attempts,
        "next_attempt_at": progress.next_attempt_at,
        "updated_at": progress.updated_at,
        "completed_at": progress.completed_at,
    }


def loan_from_row(row: LoanRow) -> Loan:
    """A stored loan as the entity."""
    terms = LoanTerms(
        Vin(row.vin), BorrowerRef(row.borrower_ref), row.origination_date, Money(row.original_amount_cents)
    )
    return Loan(
        LoanRecord(
            loan_id=row.loan_id,
            facility_id=row.facility_id,
            first_submission_id=row.first_submission_id,
            lender_loan_number=LenderLoanNumber(row.lender_loan_number),
            terms=terms,
            current_balance=Money(row.current_balance_cents),
            state=LoanState(row.state),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    )


def loan_values(loan: Loan) -> dict[str, object]:
    """Every column of a loan row."""
    record = loan.record
    return {
        "loan_id": record.loan_id,
        "facility_id": record.facility_id,
        "first_submission_id": record.first_submission_id,
        "lender_loan_number": record.lender_loan_number.value,
        "vin": record.terms.vin.value,
        "borrower_ref": record.terms.borrower_ref.value,
        "origination_date": record.terms.origination_date,
        "original_amount_cents": record.terms.original_amount.cents,
        "current_balance_cents": record.current_balance.cents,
        "state": record.state.value,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def facility_from_rows(facility_row: FacilityRow, lender_row: LenderRow) -> FacilityWithLender:
    """A facility and its lender."""
    facility = Facility(
        facility_id=facility_row.facility_id,
        lender_id=facility_row.lender_id,
        code=facility_row.code,
        name=facility_row.name,
        credit_limit_cents=facility_row.credit_limit_cents,
        advance_rate_bps=facility_row.advance_rate_bps,
        status=FacilityStatus(facility_row.status),
        opened_on=facility_row.opened_on,
    )
    lender = Lender(lender_row.lender_id, lender_row.legal_name, Lei(lender_row.lei))
    return FacilityWithLender(facility, lender)
