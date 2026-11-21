"""What the answer key expects of each tape row, and how an oracle observation is compared to it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from onepledge_datagen.application.view import KeyExpectation, KeyMessiness, KeyRow
from onepledge_datagen.domain.oracle.model import Action, CheckOutcome, Disposition, RowObservation

ACCEPTED_DISPOSITIONS = frozenset(
    {Disposition.ACCEPTED_NEW, Disposition.ACCEPTED_REPRESENTED, Disposition.RELEASE_REQUESTED}
)


@dataclass(frozen=True)
class RowExpectation:
    """The expectation of one row, from a case, a data quality entry, or the baseline rule."""

    source: str
    expected: KeyExpectation
    exact_problems: bool
    allowed_extra_fixes: frozenset[str]


def baseline_expectation(allowed_fixes: frozenset[str]) -> RowExpectation:
    """Accepted, locked, no alert; only the lender format's inherent fixes are allowed."""
    expected = KeyExpectation("ACCEPTED", (), "LOCKED", (), {}, None, None, (), (), None)
    return RowExpectation("baseline", expected, exact_problems=True, allowed_extra_fixes=allowed_fixes)


def case_expectation(row: KeyRow, allowed_fixes: frozenset[str]) -> RowExpectation:
    """A case row is compared exactly, except that rejected rows list at least the expected problems."""
    exact = row.expected.outcome != "REJECTED"
    return RowExpectation(f"{row.case_id} {row.role}", row.expected, exact, allowed_fixes)


def messiness_expectation(entry: KeyMessiness, allowed_fixes: frozenset[str]) -> RowExpectation:
    """FIXED rows are locked with exactly that problem; REJECTED rows include their problem."""
    if entry.expected_action == Action.FIXED.value:
        expected = KeyExpectation(
            "ACCEPTED", (entry.expected_problem,), "LOCKED", (), {}, None, None, (), (), None
        )
        return RowExpectation(entry.kind, expected, exact_problems=True, allowed_extra_fixes=allowed_fixes)
    expected = KeyExpectation(
        "REJECTED", (entry.expected_problem,), "REJECTED", (), {}, None, None, (), (), None
    )
    return RowExpectation(entry.kind, expected, exact_problems=False, allowed_extra_fixes=allowed_fixes)


def _problem_mismatch(expectation: RowExpectation, observation: RowObservation) -> str | None:
    observed = {issue.problem.value for issue in observation.issues}
    wanted = set(expectation.expected.cleaning_problems)
    if expectation.exact_problems:
        if observed - expectation.allowed_extra_fixes != wanted - expectation.allowed_extra_fixes:
            return f"cleaning problems {sorted(observed)} instead of {sorted(wanted)}"
        return None
    if not wanted <= observed:
        return f"cleaning problems {sorted(observed)} do not include {sorted(wanted)}"
    return None


def _check_mismatch(expected: KeyExpectation, observation: RowObservation) -> str | None:
    failed = {
        c.check_type.value: c.reason.value for c in observation.checks if c.outcome == CheckOutcome.FAIL
    }
    if set(failed) != set(expected.failed_checks):
        return f"failed checks {sorted(failed)} instead of {sorted(expected.failed_checks)}"
    for check, reason in expected.failed_check_reasons.items():
        if failed.get(check) != reason:
            return f"check {check} failed with {failed.get(check)} instead of {reason}"
    return None


def _match_mismatch(expected: KeyExpectation, observation: RowObservation) -> str | None:
    if (observation.match_kind, observation.match_basis) != (expected.match_kind, expected.match_basis):
        observed = f"{observation.match_kind}/{observation.match_basis}"
        return f"match {observed} instead of {expected.match_kind}/{expected.match_basis}"
    matched = set(observation.kinds_matched)
    if not set(expected.include) <= matched or matched & set(expected.exclude):
        rule = f"include {list(expected.include)} exclude {list(expected.exclude)}"
        return f"kinds matched {sorted(matched)} break {rule}"
    return None


def _alert_mismatch(expected: KeyExpectation, observation: RowObservation) -> str | None:
    wanted = [] if expected.alert is None else [expected.alert]
    if sorted(observation.alerts) != wanted:
        return f"alerts {observation.alerts} instead of {wanted}"
    return None


def compare(expectation: RowExpectation, observation: RowObservation) -> list[str]:
    """Every way an observation differs from its expectation (empty when it matches)."""
    expected = expectation.expected
    accepted = observation.disposition in ACCEPTED_DISPOSITIONS
    disposition = "ACCEPTED" if accepted else "REJECTED"
    problems: list[str | None] = []
    if disposition != expected.disposition:
        problems.append(f"disposition {observation.disposition.value} instead of {expected.disposition}")
    if observation.outcome.value != expected.outcome:
        problems.append(f"outcome {observation.outcome.value} instead of {expected.outcome}")
    problems.append(_problem_mismatch(expectation, observation))
    if expected.outcome != "REJECTED":
        problems.extend(
            [
                _check_mismatch(expected, observation),
                _match_mismatch(expected, observation),
                _alert_mismatch(expected, observation),
            ]
        )
    location = f"{observation.step_id} row {observation.row_number} ({expectation.source})"
    return [f"{location}: {problem}" for problem in problems if problem is not None]


def race_violations(pairs: Mapping[str, Sequence[RowObservation]]) -> list[str]:
    """Each race pair has exactly one LOCKED row with a HOLDER EXACT alert and one EXACT ASSET conflict."""
    violations = []
    for case_id, observations in pairs.items():
        locked = [o for o in observations if o.outcome.value == "LOCKED"]
        conflicts = [o for o in observations if o.outcome.value == "CONFLICT"]
        if len(locked) != 1 or len(conflicts) != 1:
            violations.append(f"{case_id}: race outcomes {[o.outcome.value for o in observations]}")
            continue
        if sorted(locked[0].alerts) != [("HOLDER", "EXACT")]:
            violations.append(f"{case_id}: the winning row has alerts {locked[0].alerts}")
        loser = conflicts[0]
        if (loser.match_kind, loser.match_basis) != ("EXACT", "ASSET") or loser.alerts != [
            ("CLAIMANT", "EXACT")
        ]:
            violations.append(
                f"{case_id}: the losing row is {loser.match_kind}/{loser.match_basis} {loser.alerts}"
            )
    return violations
