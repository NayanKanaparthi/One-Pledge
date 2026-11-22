"""File level and database level rules of the clean stage (SPEC 10.5)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date

import pytest

from bank_testkit.tapes import SUMMIT_HEADER, summit_row
from onepledge_bank_agent.domain.cleaning import CleanedRow, HeaderMap, clean_row
from onepledge_bank_agent.domain.dispositions import (
    CleanedLine,
    ExistingLoan,
    LoanEffect,
    decide_release_instruction,
    decide_rows,
)
from onepledge_bank_agent.domain.enums import CleaningProblem, LoanState, RowDisposition, TapeKind

pytestmark = pytest.mark.unit

AS_OF = date(2026, 9, 30)
LOAN = uuid.uuid4()


def line(number: int, kind: TapeKind = TapeKind.PLEDGE, **cells: str) -> CleanedLine:
    header = HeaderMap.from_header(SUMMIT_HEADER, kind)
    return CleanedLine(number, uuid.uuid4(), clean_row(summit_row(**cells), header, kind, AS_OF))


def existing(state: LoanState, *, tokens: bool, original: str = "$23,450.17") -> ExistingLoan:
    result = line(99, original=original).result
    assert isinstance(result, CleanedRow) and result.loan is not None
    return ExistingLoan(LOAN, state, result.loan.terms, tokens)


type Case = tuple[LoanState | None, bool, bool, RowDisposition, LoanEffect, CleaningProblem | None]


@pytest.mark.parametrize(
    "case",
    [
        (None, False, True, RowDisposition.ACCEPTED_NEW, LoanEffect.OPEN_LOAN, None),
        (
            LoanState.PENDING,
            False,
            False,
            RowDisposition.ACCEPTED_REPRESENTED,
            LoanEffect.REPLACE_TERMS,
            None,
        ),
        (
            LoanState.HELD_LOCAL,
            False,
            False,
            RowDisposition.ACCEPTED_REPRESENTED,
            LoanEffect.REPLACE_TERMS,
            None,
        ),
        (LoanState.PENDING, True, True, RowDisposition.ACCEPTED_REPRESENTED, LoanEffect.UPDATE_BALANCE, None),
        (
            LoanState.HELD_LOCAL,
            True,
            True,
            RowDisposition.ACCEPTED_REPRESENTED,
            LoanEffect.UPDATE_BALANCE,
            None,
        ),
        (
            LoanState.PENDING,
            True,
            False,
            RowDisposition.REJECTED,
            LoanEffect.NONE,
            CleaningProblem.TERMS_CHANGED_AFTER_TOKENIZATION,
        ),
        (LoanState.LOCKED, True, True, RowDisposition.ALREADY_LOCKED, LoanEffect.UPDATE_BALANCE, None),
        (
            LoanState.LOCKED,
            True,
            False,
            RowDisposition.REJECTED,
            LoanEffect.NONE,
            CleaningProblem.TERMS_CHANGED_WHILE_LOCKED,
        ),
        (
            LoanState.CONFLICT,
            True,
            True,
            RowDisposition.REJECTED,
            LoanEffect.NONE,
            CleaningProblem.OPEN_CONFLICT,
        ),
    ],
)
def test_each_row_of_the_existing_loan_table(case: Case) -> None:
    state, tokens, same, disposition, effect, problem = case
    known = (
        None
        if state is None
        else existing(state, tokens=tokens, original="$23,450.17" if same else "$20,000.00")
    )
    (decision,) = decide_rows([line(1)], TapeKind.PLEDGE, lambda vin: known)
    assert (decision.disposition, decision.effect) == (disposition, effect)
    assert decision.loan_id == (
        None if disposition in (RowDisposition.ACCEPTED_NEW, RowDisposition.REJECTED) else LOAN
    )
    if problem is not None:
        assert decision.issues[-1].problem is problem and decision.issues[-1].field == "row"


def test_a_repeated_vin_keeps_the_first_row_and_rejects_the_rest() -> None:
    lines = [
        line(3, borrower="c7654321"),
        line(1),
        line(2, vin="1hgcm82633a004352"),
        line(4, vin="1HGCM82633A004353"),
    ]
    decisions = decide_rows(lines, TapeKind.PLEDGE, lambda vin: None)
    assert [d.line.row_number for d in decisions] == [1, 2, 3, 4]
    assert [d.disposition for d in decisions] == [
        RowDisposition.ACCEPTED_NEW,
        RowDisposition.REJECTED,
        RowDisposition.REJECTED,
        RowDisposition.REJECTED,
    ]
    assert [i.problem for i in decisions[1].issues] == [
        CleaningProblem.VIN_CASE_NORMALISED,
        CleaningProblem.DUPLICATE_VIN_IN_FILE,
    ]
    assert decisions[2].issues[-1].problem is CleaningProblem.DUPLICATE_VIN_IN_FILE
    assert [i.problem for i in decisions[3].issues] == [CleaningProblem.VIN_CHECK_DIGIT_INVALID]


def always(loan: ExistingLoan | None) -> Callable[[str], ExistingLoan | None]:
    """A lookup that finds the same loan for every VIN."""
    return lambda vin: loan


def test_a_release_row_needs_a_locked_loan() -> None:
    release = line(1, TapeKind.RELEASE)
    locked = existing(LoanState.LOCKED, tokens=True)
    (requested,) = decide_rows([release], TapeKind.RELEASE, lambda vin: locked)
    assert requested.disposition is RowDisposition.RELEASE_REQUESTED and requested.loan_id == LOAN
    for state in (LoanState.PENDING, LoanState.CONFLICT, LoanState.HELD_LOCAL):
        (refused,) = decide_rows([release], TapeKind.RELEASE, always(existing(state, tokens=True)))
        assert (
            refused.issues[-1].problem is CleaningProblem.RELEASE_LOAN_NOT_LOCKED
            and refused.issues[-1].field == "vin"
        )
    (absent,) = decide_rows([release], TapeKind.RELEASE, lambda vin: None)
    assert absent.disposition is RowDisposition.REJECTED


def test_a_release_instruction_row_is_resolved_by_loan_id() -> None:
    instruction = CleanedLine(1, uuid.uuid4(), None)
    accepted = decide_release_instruction(instruction, existing(LoanState.LOCKED, tokens=True))
    assert (accepted.disposition, accepted.loan_id, accepted.issues) == (
        RowDisposition.RELEASE_REQUESTED,
        LOAN,
        (),
    )
    refused = decide_release_instruction(instruction, None)
    assert [i.problem for i in refused.issues] == [CleaningProblem.RELEASE_LOAN_NOT_LOCKED]
    assert refused.cleaned is None
