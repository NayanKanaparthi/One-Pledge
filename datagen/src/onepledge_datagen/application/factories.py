"""Unique identifiers and honest loans (SPEC 5.4)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta

from onepledge_datagen.domain import catalog
from onepledge_datagen.domain.entities import LoanRecord
from onepledge_datagen.domain.randomness import RandomStream, SeededStreams
from onepledge_datagen.domain.vin import ALPHABET, MODEL_YEAR_CODES, with_check_digit

DOLLARS_LOW, DOLLARS_MODE, DOLLARS_HIGH = 8_000, 21_000, 55_000
FIRST_MODEL_YEAR, LAST_MODEL_YEAR = 2015, 2026


def contains_label_word(text: str) -> bool:
    """Whether a value would spell one of the words tapes must never contain."""
    upper = text.upper()
    return any(word in upper for word in catalog.LABEL_WORDS)


class VinFactory:
    """Valid, unique VINs consistent with a manufacturer and a model year."""

    def __init__(self, rng: RandomStream) -> None:
        self._rng = rng
        self._used: set[str] = set(catalog.RESERVED_VINS)

    def new(self, vehicle_year: int) -> tuple[str, str]:
        """A fresh VIN and its make."""
        while True:
            wmi, make = self._rng.pick(catalog.MANUFACTURERS)
            middle = "".join(self._rng.pick(ALPHABET) for _ in range(5))
            plant = self._rng.pick(ALPHABET)
            draft = f"{wmi}{middle}0{MODEL_YEAR_CODES[vehicle_year]}{plant}{self._rng.digits(6)}"
            vin = with_check_digit(draft)
            if self.claim(vin):
                return vin, make

    def claim(self, vin: str) -> bool:
        """Reserve a VIN; False when it is taken or would spell a label word."""
        if vin in self._used or contains_label_word(vin):
            return False
        self._used.add(vin)
        return True


class UniqueCodes:
    """Unique borrower references and loan numbers per lender."""

    def __init__(self, rng: RandomStream) -> None:
        self._rng = rng
        self._used: set[str] = set()

    def borrower(self, lender_code: str) -> str:
        """`C` plus 7 digits (Summit) or `LK-` plus 6 digits (Lakeside)."""
        lender = catalog.lender(lender_code)
        return self._unique(lambda: lender.borrower_prefix + self._rng.digits(lender.borrower_digits))

    def loan_number(self, lender_code: str) -> str:
        """`SAF-<7 digits>-<2 digits>` (Summit) or `LMC<8 digits>` (Lakeside)."""
        if lender_code == "SUMMIT":
            return self._unique(lambda: f"SAF-{self._rng.digits(7)}-{self._rng.digits(2)}")
        return self._unique(lambda: f"LMC{self._rng.digits(8)}")

    def _unique(self, draw: Callable[[], str]) -> str:
        while True:
            value = draw()
            if value not in self._used:
                self._used.add(value)
                return value


class LoanFactory:
    """Honest loans with the distributions of SPEC 5.4."""

    def __init__(self, streams: SeededStreams) -> None:
        self._rng = streams.stream("loans")
        self.vins = VinFactory(streams.stream("vins"))
        self.codes = UniqueCodes(streams.stream("codes"))
        self._next_key = 1

    def amount_cents(self) -> int:
        """Triangular dollars (8,000 / 21,000 / 55,000) plus uniform cents."""
        dollars = self._rng.triangular_int(DOLLARS_LOW, DOLLARS_MODE, DOLLARS_HIGH)
        return dollars * 100 + self._rng.integer(0, 99)

    def create(
        self,
        lender_code: str,
        first: date,
        last: date,
        borrower_ref: str | None = None,
        amount_cents: int | None = None,
    ) -> LoanRecord:
        """A new loan originated between `first` and `last` with a fresh VIN."""
        origination = self._rng.date_between(first, last)
        year = self._rng.integer(FIRST_MODEL_YEAR, min(LAST_MODEL_YEAR, origination.year + 1))
        vin, make = self.vins.new(year)
        low, high = catalog.APR_TENTHS[lender_code]
        key = self._next_key
        self._next_key += 1
        return LoanRecord(
            key=key,
            lender_code=lender_code,
            vin=vin,
            borrower_ref=borrower_ref or self.codes.borrower(lender_code),
            loan_number=self.codes.loan_number(lender_code),
            origination=origination,
            amount_cents=amount_cents if amount_cents is not None else self.amount_cents(),
            term_months=self._rng.pick(catalog.TERMS),
            apr_tenths=self._rng.integer(low, high),
            vehicle_year=year,
            make=make,
            model=self._rng.pick(catalog.MODELS[make]),
            title_state=self._rng.weighted(catalog.TITLE_STATES, catalog.TITLE_STATE_WEIGHTS),
        )

    def rekey(self, loan: LoanRecord) -> LoanRecord:
        """A copy of a loan under a new key, to be changed with `dataclasses.replace`."""
        key = self._next_key
        self._next_key += 1
        return replace(loan, key=key)

    def honest_window(self, as_of: date) -> tuple[date, date]:
        """Origination range of an honest baseline loan first seen on a tape with this as-of date."""
        return catalog.ORIGINATION_FLOOR, as_of - timedelta(days=35)
