"""Header mapping of SPEC 10.3: which column holds which canonical field.

Header names are compared after trimming, lower casing and collapsing inner
whitespace. All other columns are kept in the raw payload and ignored.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from onepledge_bank_agent.domain.enums import TapeKind
from onepledge_bank_agent.domain.errors import TapeHeaderInvalidError

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
    return " ".join(name.strip().lower().split())


def required_fields(kind: TapeKind) -> tuple[str, ...]:
    """The canonical fields a tape of this kind must carry."""
    return RELEASE_FIELDS if kind is TapeKind.RELEASE else LOAN_FIELDS


def is_extra_key(key: str) -> bool:
    """True for the payload keys of cells beyond the header (`__extra_1`, ...)."""
    return key.startswith(EXTRA_PREFIX)


@dataclass(frozen=True)
class HeaderMap:
    """Canonical field to header text, and every header name of the tape."""

    columns: Mapping[str, str]
    names: frozenset[str]

    @property
    def lead_field(self) -> str:
        """The field whose cell is read as the first cell of a row by the totals rule."""
        return LOAN_NUMBER if LOAN_NUMBER in self.columns else VIN

    @classmethod
    def from_header(cls, header: Iterable[str], kind: TapeKind) -> HeaderMap:
        """Validate a file header (SPEC 10.1 step 2) and map it; raises `TapeHeaderInvalidError`."""
        columns: dict[str, str] = {}
        seen: set[str] = set()
        names: list[str] = []
        for name in header:
            normalised = normalise_header_name(name)
            if not normalised or normalised in seen or is_extra_key(name):
                raise TapeHeaderInvalidError
            seen.add(normalised)
            names.append(name)
            field = _ALIAS_TO_FIELD.get(normalised)
            if field is None:
                continue
            if field in columns:
                raise TapeHeaderInvalidError
            columns[field] = name
        if any(field not in columns for field in required_fields(kind)):
            raise TapeHeaderInvalidError
        return cls(MappingProxyType(columns), frozenset(names))

    @classmethod
    def from_stored_keys(cls, keys: Iterable[str]) -> HeaderMap:
        """Rebuild the map from the payload keys of a stored tape (the header was validated at intake)."""
        names = frozenset(key for key in keys if not is_extra_key(key))
        columns: dict[str, str] = {}
        for name in sorted(names):
            field = _ALIAS_TO_FIELD.get(normalise_header_name(name))
            if field is not None:
                columns[field] = name
        return cls(MappingProxyType(columns), names)
