"""The invariants of SPEC 5.8, checked on the parsed files of a data set.

Structural rules (check digits, repeated VINs, case wiring, row counts, no labels)
and the outcome rules: every row's expected outcome must follow from the rules of
SPEC sections 9 to 14, as computed by the independent oracle in `domain.oracle`.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from onepledge_datagen.application.expectations import (
    RowExpectation,
    baseline_expectation,
    case_expectation,
    compare,
    messiness_expectation,
    race_violations,
)
from onepledge_datagen.application.messiness import AMOUNT_FORMS, AMOUNT_KIND, expected_form_counts
from onepledge_datagen.application.view import DataSetView, ManifestStep
from onepledge_datagen.domain import catalog
from onepledge_datagen.domain.entities import ContractRecord, PaymentRecord, TitleRecord
from onepledge_datagen.domain.oracle.cleaning import TapeFormatError, parse_tape
from onepledge_datagen.domain.oracle.model import RowObservation
from onepledge_datagen.domain.oracle.simulation import (
    FacilityTerms,
    SimulationState,
    StepInput,
    TimelineOracle,
)
from onepledge_datagen.domain.tape_format import COLUMN_AMOUNT, COLUMN_VIN, format_for
from onepledge_datagen.domain.text import plain_list, plural
from onepledge_datagen.domain.vin import is_valid_vin

INVALID_VIN_PROBLEMS = frozenset(
    {
        "VIN_CHECK_DIGIT_INVALID",
        "VIN_LENGTH_INVALID",
        "MISSING_REQUIRED_FIELD",
        "VIN_CHARACTER_SUBSTITUTED",
        "NON_DATA_ROW",
    }
)
COPY_ROLE, ORIGINAL_ROLE = "COPY", "ORIGINAL"
DUPLICATE_ROWS = 2


class OutsideRecordsIndex:
    """The oracle's read access to the outside records of a view."""

    def __init__(
        self,
        titles: Sequence[TitleRecord],
        payments: Sequence[PaymentRecord],
        contracts: Sequence[ContractRecord],
    ) -> None:
        self._titles = {t.vin: t for t in titles}
        self._payments: dict[str, list[PaymentRecord]] = defaultdict(list)
        for payment in sorted(payments, key=lambda p: (p.vin, p.paid_on)):
            self._payments[payment.vin].append(payment)
        self._paid_dates = {vin: [p.paid_on for p in items] for vin, items in self._payments.items()}
        self._contracts: dict[str, list[ContractRecord]] = defaultdict(list)
        for contract in contracts:
            self._contracts[contract.vin].append(contract)

    def title(self, vin: str) -> TitleRecord | None:
        """The title record of a VIN."""
        return self._titles.get(vin)

    def payments(self, vin: str, first: date, last: date) -> Sequence[PaymentRecord]:
        """Payments of a VIN inside an inclusive date window."""
        dates = self._paid_dates.get(vin, [])
        return self._payments.get(vin, [])[
            bisect.bisect_left(dates, first) : bisect.bisect_right(dates, last)
        ]

    def contracts(self, vin: str) -> Sequence[ContractRecord]:
        """Contract records of a VIN."""
        return self._contracts.get(vin, [])


@dataclass
class CheckReport:
    """Violations found, the number of rows checked, and the oracle's observations (first order)."""

    violations: list[str] = field(default_factory=list)
    rows_checked: int = 0
    observations: dict[tuple[str, int], RowObservation] = field(default_factory=dict)


class InvariantChecker:
    """Runs every invariant over one data set view."""

    def check(self, view: DataSetView) -> CheckReport:
        """All structural and outcome invariants.

        The answer key may only name steps and facilities the manifest and the reference
        data list; when it names others the remaining checks have nothing to stand on, so
        those references are the violations reported (SPEC_DECISIONS 43.3).
        """
        report = CheckReport()
        report.violations.extend(self._unknown_references(view))
        if report.violations:
            return report
        records = self._tape_records(view, report)
        self._check_vins(view, records, report)
        self._check_case_wiring(view, report)
        self._check_labels(view, report)
        self._check_inherent_fixes(view, report)
        self._check_amount_forms(view, records, report)
        self._check_outcomes(view, report)
        report.violations = list(dict.fromkeys(report.violations))
        report.rows_checked = sum(len(rows) for rows in records.values())
        return report

    @staticmethod
    def _unknown_references(view: DataSetView) -> list[str]:
        steps = {step.step_id for step in view.steps}
        named = [*(row.step_id for row in view.case_rows), *(entry.step_id for entry in view.messiness)]
        problems = [
            f"the answer key names step {step_id}, which manifest.json does not list"
            for step_id in sorted(set(named) - steps)
        ]
        unknown_facilities = {step.facility_code for step in view.steps} - set(view.facilities)
        problems.extend(
            f"manifest.json names facility {code}, which reference.json does not list"
            for code in sorted(unknown_facilities)
        )
        problems.extend(
            f"manifest.json step {step.step_id} names bank {step.bank_code}, "
            f"but facility {step.facility_code} belongs to {view.facilities[step.facility_code].bank_code}"
            for step in view.steps
            if step.facility_code in view.facilities
            and view.facilities[step.facility_code].bank_code != step.bank_code
        )
        return problems

    @staticmethod
    def _tape_records(view: DataSetView, report: CheckReport) -> dict[str, tuple[tuple[str, ...], ...]]:
        records: dict[str, tuple[tuple[str, ...], ...]] = {}
        for step in view.steps:
            try:
                records[step.step_id] = parse_tape(view.tapes[step.file]).records
            except (TapeFormatError, KeyError):
                report.violations.append(f"{step.step_id}: the tape {step.file} is missing or unreadable")
                continue
            found = len(records[step.step_id])
            if found != step.data_rows:
                report.violations.append(
                    f"{step.step_id}: manifest says {plural(step.data_rows, 'row')}, the file has {found:,}"
                )
        return records

    @staticmethod
    def _check_vins(
        view: DataSetView, records: dict[str, tuple[tuple[str, ...], ...]], report: CheckReport
    ) -> None:
        planted: dict[tuple[str, int], set[str]] = defaultdict(set)
        for row in view.case_rows:
            planted[(row.step_id, row.row_number)].update(row.expected.cleaning_problems)
        for entry in view.messiness:
            planted[(entry.step_id, entry.row_number)].add(entry.expected_problem)
        allowed_owners: dict[str, set[str]] = defaultdict(set)
        for row in view.case_rows:
            allowed_owners[row.case_id].add(f"{row.step_id}:{row.row_number}")
        occurrences: dict[str, list[str]] = defaultdict(list)
        for step_id, rows in records.items():
            for number, record in enumerate(rows, start=1):
                cell = record[COLUMN_VIN] if len(record) > COLUMN_VIN else ""
                vin = cell.upper().replace(" ", "").replace("-", "")
                problems = planted.get((step_id, number), set())
                if vin:
                    occurrences[vin].append(f"{step_id}:{number}")
                _vin_rule(step_id, number, vin, problems, report)
        duplicates = {f"{m.step_id}:{m.row_number}" for m in view.messiness if m.kind == "M_DUPLICATE_ROW"}
        case_sets = list(allowed_owners.values())
        for places in occurrences.values():
            if len(places) > 1 and not _explained(set(places), case_sets, duplicates):
                report.violations.append(
                    f"a VIN appears in {plural(len(places), 'row')}, {plain_list(places)}, "
                    "with no planted reason"
                )

    @staticmethod
    def _check_case_wiring(view: DataSetView, report: CheckReport) -> None:
        order = {step.step_id: index for index, step in enumerate(view.steps)}
        seen: dict[tuple[str, int], str] = {}
        for row in view.case_rows:
            place = (row.step_id, row.row_number)
            if place in seen and seen[place] != row.case_id:
                report.violations.append(
                    f"{row.step_id} row {row.row_number} is used by {seen[place]} and {row.case_id}"
                )
            seen[place] = row.case_id
        fraud_places = {(r.step_id, r.row_number) for r in view.case_rows if r.category == "FRAUD"}
        for entry in view.messiness:
            if (entry.step_id, entry.row_number) in fraud_places:
                report.violations.append(
                    f"{entry.step_id} row {entry.row_number}: a fraud row carries messiness"
                )
        by_case: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(lambda: defaultdict(list))
        for row in view.case_rows:
            by_case[row.case_id][row.role].append((row.step_id, row.bank_code))
        for case_id, roles in by_case.items():
            for original in roles.get(ORIGINAL_ROLE, []):
                for copy in roles.get(COPY_ROLE, []):
                    if order[original[0]] >= order[copy[0]] or original[1] == copy[1]:
                        report.violations.append(
                            f"{case_id}: the original is not in an earlier step at another bank"
                        )

    @staticmethod
    def _check_labels(view: DataSetView, report: CheckReport) -> None:
        for name, text in view.label_scanned_text.items():
            upper = text.upper()
            found = [word for word in catalog.LABEL_WORDS if word in upper]
            if found:
                report.violations.append(f"{name} contains the label words {plain_list(found)}")

    @staticmethod
    def _check_amount_forms(
        view: DataSetView, records: dict[str, tuple[tuple[str, ...], ...]], report: CheckReport
    ) -> None:
        """M_AMOUNT_ALT_FORMAT rows use the three forms of SPEC 5.7 in rotation (SPEC_DECISIONS 22.11)."""
        counts = [0] * len(AMOUNT_FORMS)
        entries = [m for m in view.messiness if m.kind == AMOUNT_KIND]
        for entry in entries:
            rows = records.get(entry.step_id, ())
            cell = rows[entry.row_number - 1][COLUMN_AMOUNT] if 0 < entry.row_number <= len(rows) else ""
            forms = [i for i, form in enumerate(AMOUNT_FORMS) if form.pattern.fullmatch(cell)]
            if len(forms) != 1:
                report.violations.append(
                    f"{entry.step_id} row {entry.row_number}: the {AMOUNT_KIND} amount "
                    "is not one of the three forms"
                )
                continue
            counts[forms[0]] += 1
        if counts != expected_form_counts(len(entries)):
            shown = ", ".join(
                f"{form.name}: {count}" for form, count in zip(AMOUNT_FORMS, counts, strict=True)
            )
            report.violations.append(
                f"{AMOUNT_KIND} rows are not spread over the three forms in rotation: {shown}"
            )

    @staticmethod
    def _check_inherent_fixes(view: DataSetView, report: CheckReport) -> None:
        """The key declares, for every facility, exactly the fixes its lender's format always causes."""
        if set(view.inherent_fixes) != set(view.facilities):
            report.violations.append(
                f"answer key inherent_fixes names {plain_list(sorted(view.inherent_fixes))}, "
                f"reference.json has {plain_list(sorted(view.facilities))}"
            )
        for code, facility in view.facilities.items():
            declared = view.inherent_fixes.get(code)
            wanted = format_for(facility.lender_code).inherent_fixes
            if declared is not None and declared != wanted:
                report.violations.append(
                    f"answer key inherent_fixes of {code} is {plain_list(sorted(declared))}, "
                    f"its lender format causes {plain_list(sorted(wanted))}"
                )

    def _check_outcomes(self, view: DataSetView, report: CheckReport) -> None:
        facilities = {
            code: FacilityTerms(f.bank_code, f.lender_legal_name, f.lender_lei)
            for code, f in view.facilities.items()
        }
        oracle = TimelineOracle(facilities, OutsideRecordsIndex(view.titles, view.payments, view.contracts))
        expectations = self._expectations(view)
        for order_index, state in enumerate(self._orders(view, oracle)):
            oracle.finish(state)
            if order_index == 0:
                report.observations = state.observations
            report.violations.extend(f"order {order_index + 1}: {p}" for p in state.problems)
            for place, observation in state.observations.items():
                expectation = expectations.get(place)
                if expectation is not None:
                    report.violations.extend(compare(expectation, observation))
            races: dict[str, list[RowObservation]] = defaultdict(list)
            for row in view.case_rows:
                if row.race_group is not None and (row.step_id, row.row_number) in state.observations:
                    races[row.case_id].append(state.observations[(row.step_id, row.row_number)])
            report.violations.extend(race_violations(races))

    @staticmethod
    def _expectations(view: DataSetView) -> dict[tuple[str, int], RowExpectation]:
        fixes = {s.step_id: view.inherent_fixes.get(s.facility_code, frozenset()) for s in view.steps}
        expectations: dict[tuple[str, int], RowExpectation] = {}
        for step in view.steps:
            for number in range(1, step.data_rows + 1):
                expectations[(step.step_id, number)] = baseline_expectation(fixes[step.step_id])
        for entry in view.messiness:
            expectations[(entry.step_id, entry.row_number)] = messiness_expectation(
                entry, fixes[entry.step_id]
            )
        for row in view.case_rows:
            place = (row.step_id, row.row_number)
            if row.race_group is None:
                expectations[place] = case_expectation(row, fixes[row.step_id])
            else:
                expectations.pop(place, None)
        return expectations

    @staticmethod
    def _orders(view: DataSetView, oracle: TimelineOracle) -> list[SimulationState]:
        """One final state per order of each concurrent group (both orders of a pair)."""
        states = [oracle.new_state()]
        index = 0
        steps = list(view.steps)
        while index < len(steps):
            group = [steps[index]]
            while _continues_group(steps, index + len(group), group[0].concurrent_group):
                group.append(steps[index + len(group)])
            orders = [group, list(reversed(group))] if len(group) > 1 else [group]
            next_states = []
            for state in states:
                for order in orders:
                    branch = state.fork() if len(orders) > 1 else state
                    for step in order:
                        oracle.run_step(branch, _step_input(view, step))
                    next_states.append(branch)
            states = next_states
            index += len(group)
        return states


def _step_input(view: DataSetView, step: ManifestStep) -> StepInput:
    return StepInput(
        step.step_id,
        step.bank_code,
        step.facility_code,
        step.kind == "RELEASE",
        step.as_of,
        view.tapes[step.file],
    )


def _vin_rule(step_id: str, number: int, vin: str, problems: set[str], report: CheckReport) -> None:
    if problems & INVALID_VIN_PROBLEMS:
        if "VIN_CHECK_DIGIT_INVALID" in problems and is_valid_vin(vin):
            report.violations.append(
                f"{step_id} row {number}: a VIN planted as invalid passes the check digit"
            )
        return
    if any(char in "IOQ" for char in vin) or not is_valid_vin(vin):
        report.violations.append(f"{step_id} row {number}: a VIN not planted as invalid fails the check")


def _explained(places: set[str], case_sets: list[set[str]], duplicates: set[str]) -> bool:
    if any(places <= owned for owned in case_sets):
        return True
    return len(places) == DUPLICATE_ROWS and bool(places & duplicates)


def _continues_group(steps: Sequence[ManifestStep], position: int, group: str | None) -> bool:
    return group is not None and position < len(steps) and steps[position].concurrent_group == group
