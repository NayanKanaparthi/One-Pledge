"""`CleanTape`: the CLEANED stage (SPEC 10.4, 10.5, 13.2).

One transaction for the whole file: clean every raw row, decide every disposition,
create or update loans, store items and issues, audit each loan state change and the
stage, and set CLEANED. A crash before commit leaves nothing; the stage runs again.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import datetime

from onepledge_bank_agent.application.audit_entries import loan_state_changed, stage_advanced
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.ports import NewItem, StoredRawRow, UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.cleaning import CleanedRow, HeaderMap, LoanValues, clean_row
from onepledge_bank_agent.domain.dispositions import (
    CleanedLine,
    ExistingLoan,
    LoanEffect,
    RowDecision,
    decide_release_instruction,
    decide_rows,
)
from onepledge_bank_agent.domain.enums import CleaningAction, RowDisposition, TapeSource, TapeStatus
from onepledge_bank_agent.domain.loan import Loan, LoanDraft, StateChange
from onepledge_bank_agent.domain.tape import TapeFacts

INSTRUCTION_KEY = "loan_id"


def _instruction_loan_id(row: StoredRawRow) -> uuid.UUID | None:
    try:
        return uuid.UUID(row.payload.get(INSTRUCTION_KEY, ""))
    except ValueError:
        return None


class CleanTape:
    """Runs the clean stage of one RECEIVED submission."""

    reached = TapeStatus.CLEANED

    def __init__(self, units: UnitOfWorkFactory, runtime: Runtime) -> None:
        self._units = units
        self._clock = runtime.clock
        self._ids = runtime.ids
        self._faults = runtime.faults

    def run(self, submission_id: uuid.UUID) -> None:
        """Clean the submission if it is still RECEIVED."""
        with self._units() as unit:
            tape = unit.tapes.get(submission_id, for_update=True)
            if tape is None or tape.status is not TapeStatus.RECEIVED:
                return
            rows = unit.raw_rows.list_for(submission_id)
            decisions = self._decide(unit, tape.facts, rows)
            changes = self._apply(unit, tape.facts, decisions)
            now = self._clock.now()
            before = tape.mark_cleaned(now)
            unit.tapes.save(tape)
            for change, reason in changes:
                unit.record(loan_state_changed(change, submission_id, reason))
            unit.record(stage_advanced(submission_id, before, tape.status, _counts(decisions)))
            self._faults.reached(FaultPoint.CLEAN_BEFORE_COMMIT)
            unit.commit()

    @staticmethod
    def _decide(unit: UnitOfWork, facts: TapeFacts, rows: Sequence[StoredRawRow]) -> list[RowDecision]:
        if facts.source is TapeSource.API_INSTRUCTION:
            return _decide_instruction(unit, facts, rows)
        return _decide_file(unit, facts, rows)

    def _apply(
        self, unit: UnitOfWork, facts: TapeFacts, decisions: Sequence[RowDecision]
    ) -> list[tuple[StateChange, str]]:
        """Create and update loans, then store every item with its issues; returns the state changes."""
        now = self._clock.now()
        touched = (d.loan_id for d in decisions if d.effect is not LoanEffect.NONE and d.loan_id is not None)
        affected = unit.loans.get_many(touched, for_update=True)
        new_loans: list[Loan] = []
        changes: list[tuple[StateChange, str]] = []
        items: list[NewItem] = []
        for decision in decisions:
            values = decision.cleaned.loan if decision.cleaned is not None else None
            loan_id = decision.loan_id
            change: StateChange | None = None
            if decision.effect is LoanEffect.OPEN_LOAN and values is not None:
                loan_id = self._ids.new_id()
                draft = LoanDraft(
                    loan_id,
                    facts.facility_id,
                    facts.submission_id,
                    values.lender_loan_number,
                    values.terms,
                    values.current_balance,
                )
                loan, change = Loan.open(draft, now)
                new_loans.append(loan)
            elif decision.effect is not LoanEffect.NONE and values is not None and loan_id is not None:
                change = _update_existing(affected[loan_id], decision, values, now)
                unit.loans.save(affected[loan_id])
            if change is not None:
                changes.append((change, decision.disposition.value))
            line = decision.line
            items.append(
                NewItem(
                    self._ids.new_id(),
                    line.raw_id,
                    line.row_number,
                    decision.disposition,
                    loan_id,
                    decision.issues,
                )
            )
        unit.loans.add_many(new_loans)
        unit.items.add_many(facts.submission_id, items)
        return changes


def _update_existing(
    loan: Loan, decision: RowDecision, values: LoanValues, now: datetime
) -> StateChange | None:
    """Re-presented or already locked: new terms or a new balance; a held loan goes back to PENDING."""
    if decision.effect is LoanEffect.REPLACE_TERMS:
        loan.replace_terms(values.terms, values.lender_loan_number, values.current_balance, now)
    else:
        loan.update_balance(values.current_balance, now)
    if decision.disposition is RowDisposition.ACCEPTED_REPRESENTED:
        return loan.represent(now)
    return None


def _decide_instruction(
    unit: UnitOfWork, facts: TapeFacts, rows: Sequence[StoredRawRow]
) -> list[RowDecision]:
    """Rows `{"loan_id": ...}` of a release instruction, resolved by loan id within the facility."""
    requested = {row.raw_id: _instruction_loan_id(row) for row in rows}
    found = unit.loans.get_many(loan_id for loan_id in requested.values() if loan_id is not None)
    decisions = []
    for row in rows:
        loan_id = requested[row.raw_id]
        loan = found.get(loan_id) if loan_id is not None else None
        existing = None
        if loan is not None and loan.record.facility_id == facts.facility_id:
            existing = ExistingLoan(loan.loan_id, loan.state, loan.terms, has_tokens=True)
        decisions.append(decide_release_instruction(CleanedLine(row.row_number, row.raw_id, None), existing))
    return decisions


def _decide_file(unit: UnitOfWork, facts: TapeFacts, rows: Sequence[StoredRawRow]) -> list[RowDecision]:
    """Rows of an uploaded file: clean each, then the file and database rules."""
    header_map = HeaderMap.from_stored_keys(key for row in rows for key in row.payload)
    lines = [
        CleanedLine(
            row.row_number, row.raw_id, clean_row(row.payload, header_map, facts.kind, facts.as_of_date)
        )
        for row in rows
    ]
    vins = {line.result.vin.value for line in lines if isinstance(line.result, CleanedRow)}
    live = unit.loans.live_by_vin(facts.facility_id, vins)
    tokened = unit.tokens.loans_with_tokens(loan.loan_id for loan in live.values())

    def existing(vin: str) -> ExistingLoan | None:
        loan = live.get(vin)
        if loan is None:
            return None
        return ExistingLoan(loan.loan_id, loan.state, loan.terms, loan.loan_id in tokened)

    return decide_rows(lines, facts.kind, existing)


def _counts(decisions: Sequence[RowDecision]) -> dict[str, int]:
    by_disposition = Counter(decision.disposition for decision in decisions)
    fixed = sum(
        1
        for decision in decisions
        if decision.disposition is not RowDisposition.REJECTED
        and any(issue.action is CleaningAction.FIXED for issue in decision.issues)
    )
    return {
        "rows": len(decisions),
        "rows_rejected": by_disposition[RowDisposition.REJECTED],
        "rows_fixed": fixed,
        "accepted_new": by_disposition[RowDisposition.ACCEPTED_NEW],
        "accepted_represented": by_disposition[RowDisposition.ACCEPTED_REPRESENTED],
        "already_locked": by_disposition[RowDisposition.ALREADY_LOCKED],
        "release_requested": by_disposition[RowDisposition.RELEASE_REQUESTED],
    }
