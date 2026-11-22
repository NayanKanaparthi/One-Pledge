"""Walks a timeline of tapes through cleaning, local checks and the registry (oracle, SPEC 10 to 14).

Each bank keeps its own loans per facility with the states of SPEC 8.4. The
registry model is shared. The result is one observation per tape row, including
the alerts that end up on the row's loan.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from onepledge_datagen.domain.oracle.checks import LocalChecks, OutsideRecords, holds_locally
from onepledge_datagen.domain.oracle.cleaning import RowCleaner, TapeFormatError, parse_tape
from onepledge_datagen.domain.oracle.model import (
    Action,
    CleanedRow,
    Disposition,
    Issue,
    LoanState,
    Problem,
    RowObservation,
    RowOutcome,
    Terms,
)
from onepledge_datagen.domain.oracle.registry import Decision, RegistryModel, token_inputs


@dataclass(frozen=True)
class FacilityTerms:
    """What the bank knows about a facility's lender."""

    bank_code: str
    lender_legal_name: str
    lender_lei: str


@dataclass(frozen=True)
class StepInput:
    """One tape of the timeline."""

    step_id: str
    bank_code: str
    facility_code: str
    is_release: bool
    as_of: date
    data: bytes


@dataclass
class _Loan:
    """One loan of a simulated bank: its terms, state and whether it has been tokenised."""

    ref: tuple[str, int]
    facility_code: str
    terms: Terms
    state: LoanState = LoanState.PENDING
    tokenised: bool = False
    alerts: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class _Bank:
    """One bank's loans as the oracle tracks them, oldest first."""

    code: str
    loans: list[_Loan] = field(default_factory=list)

    def active(self, facility_code: str, vin: str) -> _Loan | None:
        """The loan of this VIN at this facility that is not RELEASED (SPEC 10.5 existing loan lookup)."""
        for loan in self.loans:
            if (
                loan.facility_code == facility_code
                and loan.terms.vin == vin
                and loan.state != LoanState.RELEASED
            ):
                return loan
        return None

    def new_loan(self, facility_code: str, terms: Terms) -> _Loan:
        """A new loan at this bank, numbered in creation order."""
        loan = _Loan((self.code, len(self.loans) + 1), facility_code, terms)
        self.loans.append(loan)
        return loan

    def loan_for_asset(self, vin: str) -> _Loan | None:
        """The tokenised loan of this VIN an alert refers to: the newest active one, else the newest."""
        matching = [loan for loan in self.loans if loan.terms.vin == vin and loan.tokenised]
        active = [loan for loan in matching if loan.state != LoanState.RELEASED]
        candidates = active or matching
        return candidates[-1] if candidates else None


@dataclass
class SimulationState:
    """Banks and registry after some steps."""

    banks: dict[str, _Bank]
    registry: RegistryModel = field(default_factory=RegistryModel)
    observations: dict[tuple[str, int], RowObservation] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def fork(self) -> SimulationState:
        """An independent copy, used to try both orders of a concurrent group."""
        return copy.deepcopy(self)


class TimelineOracle:
    """Applies the rules of SPEC 10 to 14 to tapes in order."""

    def __init__(self, facilities: Mapping[str, FacilityTerms], records: OutsideRecords) -> None:
        self._facilities = facilities
        self._checks = LocalChecks(records)

    def new_state(self) -> SimulationState:
        """Empty banks for every facility's bank."""
        banks = {terms.bank_code: _Bank(terms.bank_code) for terms in self._facilities.values()}
        return SimulationState(banks=banks)

    def run_step(self, state: SimulationState, step: StepInput) -> None:
        """Process one tape completely (all stages up to the registry decision)."""
        try:
            tape = parse_tape(step.data)
            cleaner = RowCleaner(tape.header, step.is_release, step.as_of)
        except TapeFormatError as error:
            state.problems.append(f"{step.step_id}: the tape cannot be read: {error}")
            return
        bank = state.banks[step.bank_code]
        facility = self._facilities[step.facility_code]
        rows: list[tuple[RowObservation, _Loan | None]] = []
        seen_vins: set[str] = set()
        for index, record in enumerate(tape.records, start=1):
            cleaned = cleaner.clean(record)
            observation, loan = self._dispose(bank, step, cleaned, seen_vins, index)
            state.observations[(step.step_id, index)] = observation
            rows.append((observation, loan))
        if step.is_release:
            self._release(state, bank, rows)
            return
        self._check(rows, facility, step.as_of)
        self._submit(state, bank, facility, rows)

    def _dispose(
        self, bank: _Bank, step: StepInput, cleaned: CleanedRow, seen: set[str], row_number: int
    ) -> tuple[RowObservation, _Loan | None]:
        """Cleaning result, duplicate VIN in the file and the loan lookup of SPEC 10.5 for one row."""
        issues = cleaned.issues
        if cleaned.rejected or cleaned.vin is None:
            return _rejected_row(step, row_number, issues), None
        if cleaned.vin in seen:
            return _rejected_row(
                step, row_number, issues, Issue("vin", Problem.DUPLICATE_VIN_IN_FILE, Action.REJECTED)
            ), None
        seen.add(cleaned.vin)
        existing = bank.active(step.facility_code, cleaned.vin)
        if step.is_release:
            return self._dispose_release(step, row_number, issues, existing)
        if cleaned.terms is None:
            return _rejected_row(step, row_number, issues), None
        disposition, problem, loan = _lookup_rule(bank, step.facility_code, existing, cleaned.terms)
        if problem is not None:
            return _rejected_row(step, row_number, issues, Issue("row", problem, Action.REJECTED)), None
        observation = RowObservation(step.step_id, row_number, disposition, issues)
        if loan is not None:
            observation.loan_ref = loan.ref
        return observation, loan

    @staticmethod
    def _dispose_release(
        step: StepInput, row_number: int, issues: tuple[Issue, ...], existing: _Loan | None
    ) -> tuple[RowObservation, _Loan | None]:
        """A release row asks for a LOCKED loan of the facility; anything else is rejected."""
        if existing is None or existing.state != LoanState.LOCKED:
            issue = Issue("vin", Problem.RELEASE_LOAN_NOT_LOCKED, Action.REJECTED)
            return _rejected_row(step, row_number, issues, issue), None
        observation = RowObservation(step.step_id, row_number, Disposition.RELEASE_REQUESTED, issues)
        observation.loan_ref = existing.ref
        return observation, existing

    def _check(
        self, rows: Sequence[tuple[RowObservation, _Loan | None]], facility: FacilityTerms, as_of: date
    ) -> None:
        """The four local checks for every accepted PENDING loan; a failure holds it locally."""
        for observation, loan in rows:
            accepted = observation.disposition in (Disposition.ACCEPTED_NEW, Disposition.ACCEPTED_REPRESENTED)
            if loan is None or not accepted or loan.state != LoanState.PENDING:
                continue
            observation.checks = self._checks.run(loan.terms, facility.lender_legal_name, as_of)
            if holds_locally(observation.checks):
                loan.state = LoanState.HELD_LOCAL
                observation.outcome = RowOutcome.HELD_LOCAL
            observation.local_state = loan.state

    @staticmethod
    def _submit(
        state: SimulationState,
        bank: _Bank,
        facility: FacilityTerms,
        rows: Sequence[tuple[RowObservation, _Loan | None]],
    ) -> None:
        """Every loan still PENDING after the checks goes to the registry, in row order."""
        for observation, loan in rows:
            accepted = observation.disposition in (Disposition.ACCEPTED_NEW, Disposition.ACCEPTED_REPRESENTED)
            if loan is None or not accepted or loan.state != LoanState.PENDING:
                continue
            loan.tokenised = True
            decision = state.registry.lock(bank.code, token_inputs(facility.lender_lei, loan.terms))
            _apply_lock(state, loan, observation, decision)

    @staticmethod
    def _release(
        state: SimulationState, bank: _Bank, rows: Sequence[tuple[RowObservation, _Loan | None]]
    ) -> None:
        """Release every requested loan the bank holds; a refused release is outcome OTHER."""
        for observation, loan in rows:
            if loan is None or observation.disposition != Disposition.RELEASE_REQUESTED:
                continue
            result = state.registry.release(bank.code, "AUTO|" + loan.terms.vin)
            if result == "RELEASED":
                loan.state = LoanState.RELEASED
                observation.outcome = RowOutcome.RELEASED
            else:
                observation.outcome = RowOutcome.OTHER

    def finish(self, state: SimulationState) -> None:
        """Copy every loan's alerts onto the observations of its rows."""
        loans = {loan.ref: loan for bank in state.banks.values() for loan in bank.loans}
        for observation in state.observations.values():
            if observation.loan_ref is not None:
                observation.alerts = list(loans[observation.loan_ref].alerts)


def _rejected_row(
    step: StepInput, row_number: int, issues: tuple[Issue, ...], *extra: Issue
) -> RowObservation:
    """A REJECTED observation with its issues in reporting order."""
    ordered = tuple(sorted((*issues, *extra), key=Issue.sort_key))
    return RowObservation(
        step.step_id, row_number, Disposition.REJECTED, ordered, outcome=RowOutcome.REJECTED
    )


def _lookup_rule(
    bank: _Bank, facility_code: str, existing: _Loan | None, terms: Terms
) -> tuple[Disposition, Problem | None, _Loan | None]:
    """The facility loan lookup table of SPEC 10.5 item 2."""
    if existing is None:
        return Disposition.ACCEPTED_NEW, None, bank.new_loan(facility_code, terms)
    if existing.state in (LoanState.PENDING, LoanState.HELD_LOCAL):
        if existing.tokenised and not existing.terms.same_terms(terms):
            return Disposition.REJECTED, Problem.TERMS_CHANGED_AFTER_TOKENIZATION, None
        existing.terms = terms if not existing.tokenised else existing.terms
        existing.state = LoanState.PENDING
        return Disposition.ACCEPTED_REPRESENTED, None, existing
    if existing.state == LoanState.LOCKED:
        if existing.terms.same_terms(terms):
            return Disposition.ALREADY_LOCKED, None, existing
        return Disposition.REJECTED, Problem.TERMS_CHANGED_WHILE_LOCKED, None
    return Disposition.REJECTED, Problem.OPEN_CONFLICT, None


def _apply_lock(state: SimulationState, loan: _Loan, observation: RowObservation, decision: Decision) -> None:
    """Record the registry decision on the loan and the row, and queue both alerts of a new case."""
    if decision.result == "LOCKED":
        loan.state = LoanState.LOCKED
        observation.outcome = RowOutcome.LOCKED
        return
    loan.state = LoanState.CONFLICT
    observation.outcome = RowOutcome.CONFLICT
    observation.match_kind = decision.match_kind
    observation.match_basis = decision.match_basis
    observation.kinds_matched = decision.kinds_matched
    case = decision.new_case
    if case is None:
        return
    loan.alerts.append(("CLAIMANT", case.match_kind))
    holder_bank = state.banks[case.lock.member]
    holder_loan = holder_bank.loan_for_asset(case.lock.inputs["VIN"])
    if holder_loan is None:
        state.problems.append(
            f"{observation.step_id} row {observation.row_number}: no holder loan for an alert"
        )
        return
    holder_loan.alerts.append(("HOLDER", case.match_kind))
