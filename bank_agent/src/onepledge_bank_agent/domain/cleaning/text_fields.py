"""Borrower reference and lender loan number rules of SPEC 10.4 (and their canonical forms, SPEC 9.1)."""

from __future__ import annotations

import re

from onepledge_bank_agent.domain.cleaning.header import BORROWER_REF, LOAN_NUMBER
from onepledge_bank_agent.domain.cleaning.model import FieldResult, fixed
from onepledge_bank_agent.domain.enums import CleaningProblem
from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.values import BorrowerRef, LenderLoanNumber

_NOT_BORROWER_CHARACTER = re.compile(r"[^A-Z0-9]")


def clean_borrower_ref(text: str) -> FieldResult[BorrowerRef]:
    """Upper case and every character outside A-Z0-9 removed; then the pattern must hold."""
    canonical = _NOT_BORROWER_CHARACTER.sub("", text.upper())
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
