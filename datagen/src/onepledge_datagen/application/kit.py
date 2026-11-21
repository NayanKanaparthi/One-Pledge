"""Tools shared by the planters: the loan factory, record planning and row rendering (SPEC 5.4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from onepledge_datagen.application.factories import LoanFactory
from onepledge_datagen.application.workspace import (
    ContractSpec,
    PaymentStream,
    TapeRow,
    TitleSpec,
    Workspace,
)
from onepledge_datagen.domain import catalog
from onepledge_datagen.domain.entities import LoanRecord
from onepledge_datagen.domain.randomness import RandomStream, SeededStreams

SKIP_ONE_MONTH_PER_HUNDRED = 4


@dataclass(frozen=True)
class RecordChoices:
    """Which outside records an otherwise honest loan gets."""

    title: bool = True
    lienholder: str | None = None
    payments: bool = True
    may_skip_a_month: bool = True
    contract: bool = True


class PlantingKit:
    """Creates loans, their rows and their outside record plans."""

    def __init__(self, workspace: Workspace, streams: SeededStreams, loans: LoanFactory) -> None:
        self.workspace = workspace
        self.loans = loans
        self._rng = streams.stream("record-plans")

    def rng(self, name: str) -> RandomStream:
        """A named stream for one planter."""
        return self.workspace.streams.stream(name)

    def loan_row(self, step_id: str, loan: LoanRecord) -> TapeRow:
        """Add a loan's row to a step, rendered at that step's as-of date."""
        step = self.workspace.step(step_id)
        cells = self.workspace.lender_format(step_id).loan_cells(loan, step.as_of)
        return self.workspace.add_row(step_id, cells, loan)

    def copied_row(self, step_id: str, source: TapeRow, cells: list[str] | None = None) -> TapeRow:
        """Add a row whose cells are copied from another row (optionally changed)."""
        return self.workspace.add_row(
            step_id, list(cells if cells is not None else source.cells), source.loan
        )

    def release_row(self, step_id: str, loan: LoanRecord) -> TapeRow:
        """Add a release row for a loan."""
        return self.workspace.add_row(
            step_id, self.workspace.lender_format(step_id).release_cells(loan), loan
        )

    def honest_loan(
        self, step_id: str, first: date | None = None, last: date | None = None, whole_dollars: bool = False
    ) -> LoanRecord:
        """A new honest loan of the step's lender (origination in the honest window unless given)."""
        window_first, window_last = self.loans.honest_window(self.workspace.step(step_id).as_of)
        lender = self.workspace.lender_code(step_id)
        amount = self.loans.whole_dollar_amount_cents() if whole_dollars else None
        return self.loans.create(lender, first or window_first, last or window_last, amount_cents=amount)

    def plan_records(self, loan: LoanRecord, bank_code: str, choices: RecordChoices | None = None) -> None:
        """Title, payments and contract of a loan as SPEC 5.4 describes for an honest loan."""
        chosen = choices or RecordChoices()
        lender = catalog.lender(loan.lender_code)
        plan = self.workspace.plan(loan.vin)
        if chosen.title:
            lienholder = chosen.lienholder or self._rng.pick(lender.title_spellings)
            recorded = loan.origination + timedelta(days=self._rng.integer(5, 28))
            plan.title = TitleSpec(lienholder, recorded, loan.title_state)
        if chosen.payments:
            skips = chosen.may_skip_a_month and self._rng.chance(SKIP_ONE_MONTH_PER_HUNDRED, 100)
            plan.payments.append(PaymentStream(loan, bank_code, skips_one_month=skips))
        if chosen.contract:
            self.add_contract(loan, loan.amount_cents, loan.origination)

    def add_contract(self, loan: LoanRecord, amount_cents: int, origination: date) -> None:
        """A contract record of the loan's lender, signed 0 to 2 days before origination."""
        lender = catalog.lender(loan.lender_code)
        signed = origination - timedelta(days=self._rng.integer(0, 2))
        name = self._rng.pick(lender.contract_spellings)
        self.workspace.plan(loan.vin).contracts.append(ContractSpec(name, amount_cents, signed))
