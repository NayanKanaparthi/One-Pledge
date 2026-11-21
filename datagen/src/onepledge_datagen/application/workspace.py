"""Build-time state of one data set: rows per step, vehicle record plans, planted cases and messiness."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from onepledge_datagen.domain import catalog
from onepledge_datagen.domain.entities import LoanRecord, StepDefinition
from onepledge_datagen.domain.errors import PlanError
from onepledge_datagen.domain.randomness import SeededStreams
from onepledge_datagen.domain.tape_format import LenderFormat, format_for


@dataclass(frozen=True)
class Expectation:
    """The expected outcome of one case row (SPEC 5.10)."""

    outcome: str
    disposition: str = "ACCEPTED"
    cleaning_problems: tuple[str, ...] = ()
    failed_checks: tuple[str, ...] = ()
    failed_check_reasons: tuple[tuple[str, str], ...] = ()
    match_kind: str | None = None
    match_basis: str | None = None
    kinds_matched_include: tuple[str, ...] = ()
    kinds_matched_exclude: tuple[str, ...] = ()
    alert: tuple[str, str] | None = None


LOCKED = Expectation("LOCKED")


@dataclass
class TapeRow:
    """One data row of a tape. `row_number` is assigned when the timeline is laid out."""

    row_id: int
    step_id: str
    cells: list[str]
    loan: LoanRecord | None
    row_number: int = 0


@dataclass(frozen=True)
class PaymentStream:
    """Monthly payments of one loan: first due 30 days after origination, until a limit."""

    loan: LoanRecord
    deposit_bank_code: str
    skips_one_month: bool = False
    due_before: date | None = None
    paid_on_or_before: date | None = None


@dataclass(frozen=True)
class ContractSpec:
    """One contract record to create for a vehicle."""

    lender_name: str
    amount_cents: int
    signed_on: date


@dataclass(frozen=True)
class TitleSpec:
    """The title record of a vehicle."""

    lienholder_name: str
    lien_recorded_on: date
    state: str


@dataclass
class VehiclePlan:
    """Everything outside records will hold about one VIN."""

    vin: str
    title: TitleSpec | None = None
    payments: list[PaymentStream] = field(default_factory=list)
    contracts: list[ContractSpec] = field(default_factory=list)


@dataclass
class CaseRow:
    """One row of a planted case with its role and expectation."""

    role: str
    row: TapeRow
    expected: Expectation


@dataclass
class PlantedCase:
    """One planted case of SPEC 5.7."""

    case_id: str
    case_type: str
    category: str
    rows: list[CaseRow]
    race_group: str | None = None


@dataclass
class PlantedMessiness:
    """One data quality row of SPEC 5.7."""

    kind: str
    row: TapeRow
    expected_action: str
    expected_problem: str


class Workspace:
    """Mutable state shared by the builders and planters of one data set."""

    def __init__(self, streams: SeededStreams, steps: tuple[StepDefinition, ...], timeline_end: date) -> None:
        self.streams = streams
        self.steps = {step.step_id: step for step in steps}
        self.step_order = tuple(step.step_id for step in steps)
        self.timeline_end = timeline_end
        self.rows: dict[str, list[TapeRow]] = {step.step_id: [] for step in steps}
        self.plans: dict[str, VehiclePlan] = {}
        self.cases: list[PlantedCase] = []
        self.messiness: list[PlantedMessiness] = []
        self.baseline_rows: dict[str, list[TapeRow]] = {step.step_id: [] for step in steps}
        self._next_row = 1
        self._case_numbers: dict[str, int] = {}

    def step(self, step_id: str) -> StepDefinition:
        """A step of this data set."""
        return self.steps[step_id]

    def lender_code(self, step_id: str) -> str:
        """The lender of a step's facility."""
        return catalog.facility(self.steps[step_id].facility_code).lender_code

    def lender_format(self, step_id: str) -> LenderFormat:
        """The tape format of a step's lender."""
        return format_for(self.lender_code(step_id))

    def add_row(self, step_id: str, cells: list[str], loan: LoanRecord | None) -> TapeRow:
        """Append a row to a step's tape (order is shuffled later)."""
        row = TapeRow(self._next_row, step_id, cells, loan)
        self._next_row += 1
        self.rows[step_id].append(row)
        return row

    def plan(self, vin: str) -> VehiclePlan:
        """The record plan of a VIN, created on first use."""
        return self.plans.setdefault(vin, VehiclePlan(vin))

    def take_baseline(
        self, step_id: str, count: int, accept: Callable[[TapeRow], bool] | None = None
    ) -> list[TapeRow]:
        """Remove `count` random unused baseline rows of a step (they become case rows)."""
        pool = self.baseline_rows[step_id]
        candidates = [row for row in pool if accept is None or accept(row)]
        if len(candidates) < count:
            raise PlanError(
                f"Step {step_id} has {len(candidates)} suitable baseline rows; {count} are needed."
            )
        chosen = self.streams.stream(f"originals:{step_id}:{len(pool)}").sample(candidates, count)
        chosen_ids = {row.row_id for row in chosen}
        self.baseline_rows[step_id] = [row for row in pool if row.row_id not in chosen_ids]
        return sorted(chosen, key=lambda row: row.row_id)

    def add_case(
        self, case_type: str, category: str, rows: list[CaseRow], race_group: str | None = None
    ) -> PlantedCase:
        """Record a planted case with the next id of its type."""
        number = self._case_numbers.get(case_type, 0) + 1
        self._case_numbers[case_type] = number
        case = PlantedCase(f"{case_type}-{number:04d}", case_type, category, rows, race_group)
        self.cases.append(case)
        return case
