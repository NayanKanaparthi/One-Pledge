"""Level payment amortisation in integer cents, computed with Decimal (SPEC 5.4)."""

from __future__ import annotations

import math
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Context, Decimal

_CONTEXT = Context(prec=40)
_CENT = Decimal(1)
_TENTHS_PER_YEAR_RATE = Decimal(12000)


def _monthly_rate(apr_tenths: int) -> Decimal:
    return _CONTEXT.divide(Decimal(apr_tenths), _TENTHS_PER_YEAR_RATE)


def monthly_payment_cents(principal_cents: int, apr_tenths: int, term_months: int) -> int:
    """The level monthly payment, rounded half up to the cent."""
    rate = _monthly_rate(apr_tenths)
    growth = _CONTEXT.power(1 + rate, term_months)
    payment = _CONTEXT.divide(_CONTEXT.multiply(Decimal(principal_cents) * rate, growth), growth - 1)
    return math.floor(payment.quantize(_CENT, rounding=ROUND_HALF_UP, context=_CONTEXT))


def balance_after_months(principal_cents: int, apr_tenths: int, term_months: int, months: int) -> int:
    """Outstanding balance after `months` level payments, at least 1 cent and never above the principal."""
    if months <= 0:
        return principal_cents
    if months >= term_months:
        return 1
    rate = _monthly_rate(apr_tenths)
    payment = Decimal(monthly_payment_cents(principal_cents, apr_tenths, term_months))
    growth = _CONTEXT.power(1 + rate, months)
    balance = Decimal(principal_cents) * growth - _CONTEXT.divide(payment * (growth - 1), rate)
    cents = math.floor(balance.quantize(_CENT, rounding=ROUND_FLOOR, context=_CONTEXT))
    return min(max(cents, 1), principal_cents)
