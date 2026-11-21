"""File level and database level rules of the clean stage (SPEC 10.5), as pure decisions.

Applied in row order to rows that passed the row and field rules: a repeated VIN is
rejected after its first row, then each row is compared with the facility's loan for
that VIN that is not RELEASED.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

from onepledge_bank_agent.domain.cleaning.header import ROW, VIN
from onepledge_bank_agent.domain.cleaning.model import CleanedRow, RejectedRow, rejected
from onepledge_bank_agent.domain.enums import CleaningProblem, LoanState, RowDisposition, TapeKind
from onepledge_bank_agent.domain.values import CleaningIssue, LoanTerms


class LoanEffect(StrEnum):
    """What the clean stage does to the loan table for one row."""

    NONE = "NONE"
    OPEN_LOAN = "OPEN_LOAN"
    REPLACE_TERMS = "REPLACE_TERMS"
    UPDATE_BALANCE = "UPDATE_BALANCE"


@dataclass(frozen=True)
class ExistingLoan:
    """The facility's loan for a VIN that is not RELEASED, as the rules need it."""

    loan_id: uuid.UUID
    state: LoanState
    terms: LoanTerms
    has_tokens: bool


@dataclass(frozen=True)
class CleanedLine:
    """The cleaning result of one raw row, with its row number."""

    row_number: int
    raw_id: uuid.UUID
    result: CleanedRow | RejectedRow | None

    @property
    def issues(self) -> tuple[CleaningIssue, ...]:
        """The issues cleaning found (none for a release instruction row, which has no cell to clean)."""
        return () if self.result is None else self.result.issues


@dataclass(frozen=True)
class RowDecision:
    """The disposition of one raw row and what it does to the loans."""

    line: CleanedLine
    disposition: RowDisposition
    issues: tuple[CleaningIssue, ...]
    effect: LoanEffect
    loan_id: uuid.UUID | None

    @property
    def cleaned(self) -> CleanedRow | None:
        """The canonical values when the row passed the row and field rules."""
        return self.line.result if isinstance(self.line.result, CleanedRow) else None


def _reject(line: CleanedLine, extra: CleaningIssue | None = None) -> RowDecision:
    issues = (*line.issues, extra) if extra is not None else line.issues
    return RowDecision(line, RowDisposition.REJECTED, issues, LoanEffect.NONE, None)


def _accept(
    line: CleanedLine, disposition: RowDisposition, effect: LoanEffect, loan_id: uuid.UUID | None
) -> RowDecision:
    return RowDecision(line, disposition, line.issues, effect, loan_id)


def _represented(line: CleanedLine, existing: ExistingLoan, same_terms: bool) -> RowDecision:
    if not existing.has_tokens:
        return _accept(line, RowDisposition.ACCEPTED_REPRESENTED, LoanEffect.REPLACE_TERMS, existing.loan_id)
    if same_terms:
        return _accept(line, RowDisposition.ACCEPTED_REPRESENTED, LoanEffect.UPDATE_BALANCE, existing.loan_id)
    return _reject(line, rejected(ROW, CleaningProblem.TERMS_CHANGED_AFTER_TOKENIZATION))


def _pledge_decision(line: CleanedLine, cleaned: CleanedRow, existing: ExistingLoan | None) -> RowDecision:
    if existing is None:
        return _accept(line, RowDisposition.ACCEPTED_NEW, LoanEffect.OPEN_LOAN, None)
    same_terms = cleaned.loan is not None and cleaned.loan.terms == existing.terms
    if existing.state in (LoanState.PENDING, LoanState.HELD_LOCAL):
        return _represented(line, existing, same_terms)
    if existing.state is LoanState.LOCKED:
        if same_terms:
            return _accept(line, RowDisposition.ALREADY_LOCKED, LoanEffect.UPDATE_BALANCE, existing.loan_id)
        return _reject(line, rejected(ROW, CleaningProblem.TERMS_CHANGED_WHILE_LOCKED))
    return _reject(line, rejected(ROW, CleaningProblem.OPEN_CONFLICT))


def _release_decision(line: CleanedLine, existing: ExistingLoan | None) -> RowDecision:
    if existing is not None and existing.state is LoanState.LOCKED:
        return _accept(line, RowDisposition.RELEASE_REQUESTED, LoanEffect.NONE, existing.loan_id)
    return _reject(line, rejected(VIN, CleaningProblem.RELEASE_LOAN_NOT_LOCKED))


def decide_rows(
    lines: Iterable[CleanedLine], kind: TapeKind, existing: Callable[[str], ExistingLoan | None]
) -> list[RowDecision]:
    """One decision per row, in row number order. `existing(vin)` looks up the facility's live loan."""
    decisions: list[RowDecision] = []
    seen: set[str] = set()
    for line in sorted(lines, key=lambda item: item.row_number):
        result = line.result
        if not isinstance(result, CleanedRow):
            decisions.append(_reject(line))
            continue
        vin = result.vin.value
        if vin in seen:
            decisions.append(_reject(line, rejected(VIN, CleaningProblem.DUPLICATE_VIN_IN_FILE)))
            continue
        seen.add(vin)
        if kind is TapeKind.RELEASE:
            decisions.append(_release_decision(line, existing(vin)))
        else:
            decisions.append(_pledge_decision(line, result, existing(vin)))
    return decisions


def decide_release_instruction(line: CleanedLine, existing: ExistingLoan | None) -> RowDecision:
    """A row `{"loan_id": ...}` of a release instruction, resolved by loan id with the release rule."""
    return _release_decision(line, existing)
