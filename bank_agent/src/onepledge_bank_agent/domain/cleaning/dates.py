"""Origination date rules of SPEC 10.4. The as-of date of the tape is the upper bound."""

from __future__ import annotations

import re
from datetime import date
from types import MappingProxyType

from onepledge_bank_agent.domain.cleaning.header import ORIGINATION_DATE
from onepledge_bank_agent.domain.cleaning.model import FieldResult, fixed
from onepledge_bank_agent.domain.enums import CleaningProblem
from onepledge_bank_agent.domain.text_rules import ascii_upper, parse_integer

EARLIEST_DATE = date(2000, 1, 1)
_ISO = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
_US = re.compile(r"^([0-9]{2})/([0-9]{2})/([0-9]{4})$")
_DAY_MONTH_YEAR = re.compile(r"^([0-9]{2})-([A-Za-z]{3})-([0-9]{4})$")
_MONTHS = MappingProxyType(
    {
        name: number
        for number, name in enumerate(
            ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1
        )
    }
)


def _parse(text: str) -> tuple[date, bool] | None:
    """The date and whether it was written in the fixable `DD-Mon-YYYY` form; None when unreadable."""
    try:
        if match := _ISO.fullmatch(text):
            return date(_number(match[1]), _number(match[2]), _number(match[3])), False
        if match := _US.fullmatch(text):
            return date(_number(match[3]), _number(match[1]), _number(match[2])), False
        match = _DAY_MONTH_YEAR.fullmatch(text)
        if match and ascii_upper(match[2]) in _MONTHS:
            return date(_number(match[3]), _MONTHS[ascii_upper(match[2])], _number(match[1])), True
    except ValueError:
        return None
    return None


def _number(digits: str) -> int:
    return parse_integer(digits, ORIGINATION_DATE, 0)


def clean_origination_date(text: str, as_of_date: date) -> FieldResult[date]:
    """`YYYY-MM-DD` or `MM/DD/YYYY` (`DD-Mon-YYYY` is fixed); not after the as-of date, not before 2000."""
    parsed = _parse(text)
    if parsed is None:
        return FieldResult.refused(ORIGINATION_DATE, CleaningProblem.DATE_UNPARSEABLE)
    value, reformatted = parsed
    issues = (fixed(ORIGINATION_DATE, CleaningProblem.DATE_FORMAT_NORMALISED),) if reformatted else ()
    if value > as_of_date:
        return FieldResult.refused(ORIGINATION_DATE, CleaningProblem.DATE_IN_FUTURE, *issues)
    if value < EARLIEST_DATE:
        return FieldResult.refused(ORIGINATION_DATE, CleaningProblem.DATE_TOO_OLD, *issues)
    return FieldResult(value, issues)
