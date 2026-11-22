"""The two lender tape formats of SPEC 5.9 and how one loan becomes one row of cells."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from onepledge_datagen.domain.amortisation import balance_after_months
from onepledge_datagen.domain.calendar import iso_date, us_date, whole_months_between
from onepledge_datagen.domain.entities import LoanRecord
from onepledge_datagen.domain.money import dollar_amount, plain_amount
from onepledge_datagen.domain.text_values import digits_value, is_ascii_digits

COLUMN_LOAN_NUMBER = 0
COLUMN_VIN = 1
COLUMN_BORROWER = 2
COLUMN_ORIGINATION = 3
COLUMN_AMOUNT = 4
COLUMN_BALANCE = 5
COLUMN_COUNT = 12
TOTALS_MARKER = "TOTAL"
CENT_DIGITS = 2
ASCII_WHITESPACE = " \t\r\n"


@dataclass(frozen=True)
class LenderFormat:
    """How one lender writes its tapes."""

    lender_code: str
    header: tuple[str, ...]
    format_date: Callable[[date], str]
    format_amount: Callable[[int], str]
    byte_order_mark: bool
    line_ending: str
    totals_row: bool
    always_quoted_columns: frozenset[int]
    inherent_fixes: frozenset[str]

    def loan_cells(self, loan: LoanRecord, as_of: date) -> list[str]:
        """The twelve cells of a loan on a tape with this as-of date."""
        months = whole_months_between(loan.origination, as_of)
        balance = balance_after_months(loan.amount_cents, loan.apr_tenths, loan.term_months, months)
        return [
            loan.loan_number,
            loan.vin,
            loan.borrower_ref,
            self.format_date(loan.origination),
            self.format_amount(loan.amount_cents),
            self.format_amount(balance),
            f"{loan.apr_tenths // 10}.{loan.apr_tenths % 10}",
            str(loan.term_months),
            str(loan.vehicle_year),
            loan.make,
            loan.model,
            loan.title_state,
        ]

    def release_cells(self, loan: LoanRecord) -> list[str]:
        """A release row: only the loan number and the VIN are filled."""
        cells = [""] * COLUMN_COUNT
        cells[COLUMN_LOAN_NUMBER] = loan.loan_number
        cells[COLUMN_VIN] = loan.vin
        return cells

    def totals_cells(self, balance_cents: int) -> list[str]:
        """The trailing totals row: `TOTAL`, the balance sum, other cells empty."""
        cells = [""] * COLUMN_COUNT
        cells[COLUMN_LOAN_NUMBER] = TOTALS_MARKER
        cells[COLUMN_BALANCE] = self.format_amount(balance_cents)
        return cells


SUMMIT_FORMAT = LenderFormat(
    lender_code="SUMMIT",
    header=(
        "Loan #",
        "VIN",
        "Cust ID",
        "Orig Date",
        "Orig Amt",
        "Curr Bal",
        "APR",
        "Term",
        "Year",
        "Make",
        "Model",
        "State",
    ),
    format_date=us_date,
    format_amount=dollar_amount,
    byte_order_mark=True,
    line_ending="\r\n",
    totals_row=True,
    always_quoted_columns=frozenset({COLUMN_AMOUNT, COLUMN_BALANCE}),
    inherent_fixes=frozenset(),
)
LAKESIDE_FORMAT = LenderFormat(
    lender_code="LAKESIDE",
    header=(
        "loan_id",
        "vehicle_vin",
        "borrower_id",
        "origination_date",
        "original_amount",
        "current_balance",
        "apr_pct",
        "term_months",
        "vehicle_year",
        "vehicle_make",
        "vehicle_model",
        "title_state",
    ),
    format_date=iso_date,
    format_amount=plain_amount,
    byte_order_mark=False,
    line_ending="\n",
    totals_row=False,
    always_quoted_columns=frozenset(),
    inherent_fixes=frozenset({"BORROWER_REF_NORMALISED"}),
)


def format_for(lender_code: str) -> LenderFormat:
    """The tape format of a lender."""
    return SUMMIT_FORMAT if lender_code == SUMMIT_FORMAT.lender_code else LAKESIDE_FORMAT


def balance_cents(cells: list[str]) -> int:
    """The balance of rendered cells in either lender's format, or 0 when the cell is not an amount."""
    text = cells[COLUMN_BALANCE].strip(ASCII_WHITESPACE).replace("$", "").replace(",", "")
    whole, _, fraction = text.partition(".")
    if not is_ascii_digits(whole) or len(fraction) != CENT_DIGITS or not is_ascii_digits(fraction):
        return 0
    return digits_value(whole) * 100 + digits_value(fraction)
