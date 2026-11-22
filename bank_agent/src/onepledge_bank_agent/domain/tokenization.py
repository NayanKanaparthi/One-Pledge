"""Token inputs and token derivation (SPEC 9.2, 9.3). Pure rules; the OPRF itself lives in infrastructure.

For one loan the eight canonical value strings are built from the canonical VIN `V`,
the lender namespaced borrower `B = LEI + "|" + borrower_ref`, the original amount `C`
in cents and the origination date `D`. Each (kind, value) pair becomes the input bytes
`b"onepledge:v1:" + KIND + b"\\x1f" + value`, which the bank blinds and sends through
both key holders. The unblinded point `U` gives the token
`sha256(b"onepledge:v1:token\\x1f" + KIND + b"\\x1f" + U)` in lower case hex.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from onepledge_bank_agent.domain.enums import TokenKind
from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.values import Lei, LoanTerms

INPUT_PREFIX = b"onepledge:v1:"
TOKEN_PREFIX = b"onepledge:v1:token"
SEPARATOR = b"\x1f"
ASSET_CLASS = "AUTO"
CENTS_PER_HUNDRED_DOLLARS = 10_000
CENTS_PER_THOUSAND_DOLLARS = 100_000
POINT_BYTES = 32
TOKENS_PER_LOAN = len(TokenKind)


@dataclass(frozen=True, repr=False)
class TokenInput:
    """One kind and its canonical value string. The value is raw loan data: `repr` is masked."""

    kind: TokenKind
    value: str

    def oprf_input(self) -> bytes:
        """The bytes hashed to a group point: prefix, kind, unit separator, value."""
        return INPUT_PREFIX + self.kind.value.encode("ascii") + SEPARATOR + self.value.encode("utf-8")

    def __repr__(self) -> str:
        """Kind only."""
        return f"TokenInput({self.kind.value}, ***)"


def token_inputs(terms: LoanTerms, lender_lei: Lei) -> tuple[TokenInput, ...]:
    """The eight inputs of one loan, in the fixed kind order of `TokenKind`."""
    vin = terms.vin.value
    borrower = f"{lender_lei.value}|{terms.borrower_ref.value}"
    cents = terms.original_amount.cents
    day = terms.origination_date.isoformat()
    values = {
        TokenKind.ASSET: f"{ASSET_CLASS}|{vin}",
        TokenKind.VIN: vin,
        TokenKind.BORROWER: borrower,
        TokenKind.AMOUNT_EXACT: f"{borrower}|{cents}",
        TokenKind.AMOUNT_100: f"{borrower}|{cents // CENTS_PER_HUNDRED_DOLLARS}",
        TokenKind.AMOUNT_1000: f"{borrower}|{cents // CENTS_PER_THOUSAND_DOLLARS}",
        TokenKind.DATE_DAY: f"{borrower}|{day}",
        TokenKind.DATE_MONTH: f"{borrower}|{day[:7]}",
    }
    return tuple(TokenInput(kind, values[kind]) for kind in TokenKind)


def token_from_point(kind: TokenKind, unblinded_point: bytes) -> str:
    """The 64 hex character token of one kind from the 32 byte unblinded point `k2 * k1 * P`."""
    if len(unblinded_point) != POINT_BYTES:
        raise InvalidValueError("unblinded_point", "OUT_OF_RANGE")
    material = TOKEN_PREFIX + SEPARATOR + kind.value.encode("ascii") + SEPARATOR + unblinded_point
    return hashlib.sha256(material).hexdigest()
