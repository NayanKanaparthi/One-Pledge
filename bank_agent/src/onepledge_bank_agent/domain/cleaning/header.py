"""Header mapping of SPEC 10.3: which column holds which canonical field.

Header names are compared after trimming, lower casing and collapsing inner
whitespace. A field is read from its column position in each row's cells; all other
columns are stored verbatim and ignored.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from onepledge_bank_agent.domain.enums import TapeKind
from onepledge_bank_agent.domain.errors import TapeHeaderInvalidError
from onepledge_bank_agent.domain.text_rules import ascii_lower, split_ascii_whitespace

LOAN_NUMBER = "lender_loan_number"
VIN = "vin"
BORROWER_REF = "borrower_ref"
ORIGINATION_DATE = "origination_date"
ORIGINAL_AMOUNT = "original_amount"
CURRENT_BALANCE = "current_balance"
ROW = "row"
EXTRA_PREFIX = "__extra_"

HEADER_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        LOAN_NUMBER: ("loan #", "loan_id", "loan number", "loan_number"),
        VIN: ("vin", "vehicle_vin"),
        BORROWER_REF: ("cust id", "borrower_id", "customer id", "borrower_ref"),
        ORIGINATION_DATE: ("orig date", "origination_date"),
        ORIGINAL_AMOUNT: ("orig amt", "original_amount"),
        CURRENT_BALANCE: ("curr bal", "current_balance"),
    }
)
LOAN_FIELDS: tuple[str, ...] = tuple(HEADER_ALIASES)
RELEASE_FIELDS: tuple[str, ...] = (VIN,)
_ALIAS_TO_FIELD: Mapping[str, str] = MappingProxyType(
    {alias: field for field, aliases in HEADER_ALIASES.items() for alias in aliases}
)


def normalise_header_name(name: str) -> str:
    """Trim, lower case, collapse inner whitespace."""
    return " ".join(split_ascii_whitespace(ascii_lower(name)))


def required_fields(kind: TapeKind) -> tuple[str, ...]:
    """The canonical fields a tape of this kind must carry."""
    return RELEASE_FIELDS if kind is TapeKind.RELEASE else LOAN_FIELDS


def is_extra_key(key: str) -> bool:
    """True for the payload keys of cells beyond the header (`__extra_1`, ...)."""
    return key.startswith(EXTRA_PREFIX)


@dataclass(frozen=True)
class HeaderMap:
    """Canonical field to its column position, and the number of header cells."""

    columns: Mapping[str, int]
    width: int

    @classmethod
    def from_header(cls, header: Sequence[str], kind: TapeKind) -> HeaderMap:
        """Validate a file header (SPEC 10.1 step 2) and map it; raises `TapeHeaderInvalidError`."""
        columns: dict[str, int] = {}
        seen: set[str] = set()
        for position, name in enumerate(header):
            normalised = normalise_header_name(name)
            if not normalised or normalised in seen or is_extra_key(name):
                raise TapeHeaderInvalidError
            seen.add(normalised)
            field = _ALIAS_TO_FIELD.get(normalised)
            if field is None:
                continue
            if field in columns:
                raise TapeHeaderInvalidError
            columns[field] = position
        if any(field not in columns for field in required_fields(kind)):
            raise TapeHeaderInvalidError
        return cls(MappingProxyType(columns), len(header))
