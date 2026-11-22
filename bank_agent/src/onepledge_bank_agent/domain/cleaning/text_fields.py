"""Borrower reference and lender loan number rules of SPEC 10.4 (and their canonical forms, SPEC 9.1)."""

from __future__ import annotations

from onepledge_bank_agent.domain.cleaning.header import BORROWER_REF, LOAN_NUMBER
from onepledge_bank_agent.domain.cleaning.model import FieldResult, fixed
from onepledge_bank_agent.domain.enums import CleaningProblem
from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.text_rules import ascii_upper
from onepledge_bank_agent.domain.values import BorrowerRef, LenderLoanNumber

BORROWER_SEPARATORS = frozenset(" -_./")


def clean_borrower_ref(text: str) -> FieldResult[BorrowerRef]:
    """SPEC_DECISIONS 83: normalise (ASCII upper case, the separators space, `-`, `_`, `.`, `/` removed),
    record the normalisation when it changed the cell, then validate the pattern; a character outside
    `A-Za-z0-9` is never dropped, so it fails the pattern with the normalisation still recorded."""
    canonical = ascii_upper("".join(char for char in text if char not in BORROWER_SEPARATORS))
    issues = () if canonical == text else (fixed(BORROWER_REF, CleaningProblem.BORROWER_REF_NORMALISED),)
    try:
        return FieldResult(BorrowerRef(canonical), issues)
    except InvalidValueError:
        return FieldResult.refused(BORROWER_REF, CleaningProblem.BORROWER_REF_INVALID, *issues)


def clean_loan_number(text: str) -> FieldResult[LenderLoanNumber]:
    """The lender loan number pattern; nothing is fixed."""
    try:
        return FieldResult(LenderLoanNumber(text))
    except InvalidValueError:
        return FieldResult.refused(LOAN_NUMBER, CleaningProblem.LOAN_NUMBER_INVALID)
