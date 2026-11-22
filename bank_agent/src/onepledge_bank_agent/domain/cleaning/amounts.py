"""Amount rules of SPEC 10.4: formats, sign, the original amount range and the balance rules."""

from __future__ import annotations

import re

from onepledge_bank_agent.domain.cleaning.header import CURRENT_BALANCE, ORIGINAL_AMOUNT
from onepledge_bank_agent.domain.cleaning.model import FieldResult, fixed, rejected
from onepledge_bank_agent.domain.enums import CleaningProblem
from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.values import CleaningIssue, Money

MIN_ORIGINAL = Money(10_000)
MAX_ORIGINAL = Money(25_000_000)
_INTEGER_PART = r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)"
_EXACT = re.compile(r"^\$?" + _INTEGER_PART + r"\.([0-9]{2})$")
_FIXABLE = re.compile(r"^\$?" + _INTEGER_PART + r"(?:\.([0-9]))?$")


def _to_money(integer_part: str, fraction: str) -> Money:
    """Dollars with thousands separators and up to two fraction digits, as exact cents."""
    return Money.parse_decimal(integer_part.replace(",", "") + ("." + fraction if fraction else ""))


def clean_amount(field: str, text: str) -> FieldResult[Money]:
    """`d+.dd` or `$1,234.56` as is; no or one decimal is fixed; a leading minus is out of range."""
    negative = text.startswith("-")
    body = text[1:] if negative else text
    issues: tuple[CleaningIssue, ...] = ()
    if match := _EXACT.fullmatch(body):
        parts = (match[1], match[2])
    elif match := _FIXABLE.fullmatch(body):
        parts = (match[1], match[2] or "")
        issues = (fixed(field, CleaningProblem.AMOUNT_FORMAT_NORMALISED),)
    else:
        return FieldResult.refused(field, CleaningProblem.AMOUNT_UNPARSEABLE)
    if negative:
        return FieldResult.refused(field, CleaningProblem.AMOUNT_OUT_OF_RANGE, *issues)
    try:
        return FieldResult(_to_money(*parts), issues)
    except InvalidValueError:
        return FieldResult.refused(field, CleaningProblem.AMOUNT_OUT_OF_RANGE, *issues)


def balance_rules(original: Money | None, balance: Money | None) -> tuple[CleaningIssue, ...]:
    """Rules across the two amounts, applied to the values that parsed."""
    issues: list[CleaningIssue] = []
    if original is not None and not MIN_ORIGINAL <= original <= MAX_ORIGINAL:
        issues.append(rejected(ORIGINAL_AMOUNT, CleaningProblem.AMOUNT_OUT_OF_RANGE))
    if balance is not None and balance.cents == 0:
        issues.append(rejected(CURRENT_BALANCE, CleaningProblem.ZERO_BALANCE))
    if original is not None and balance is not None and balance > original:
        issues.append(rejected(CURRENT_BALANCE, CleaningProblem.BALANCE_EXCEEDS_ORIGINAL))
    return tuple(issues)
