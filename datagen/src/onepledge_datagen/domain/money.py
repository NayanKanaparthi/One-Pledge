"""Integer cent amounts and the ways lenders write them. No floats anywhere."""

from __future__ import annotations

CENTS_PER_DOLLAR = 100


def plain_amount(cents: int) -> str:
    """`23450.17`."""
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), CENTS_PER_DOLLAR)
    return f"{sign}{whole}.{fraction:02d}"


def dollar_amount(cents: int) -> str:
    """`$23,450.17`."""
    whole, fraction = divmod(cents, CENTS_PER_DOLLAR)
    return f"${whole:,}.{fraction:02d}"


def dollar_amount_without_cents(cents: int) -> str:
    """`$23,450` for a whole dollar amount."""
    return f"${cents // CENTS_PER_DOLLAR:,}"


def amount_without_decimals(cents: int) -> str:
    """`23450` for a whole dollar amount."""
    return str(cents // CENTS_PER_DOLLAR)


def amount_with_one_decimal(cents: int) -> str:
    """`23450.5` for an amount whose cents are a multiple of ten."""
    whole, fraction = divmod(cents, CENTS_PER_DOLLAR)
    return f"{whole}.{fraction // 10}"
