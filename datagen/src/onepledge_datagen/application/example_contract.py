"""The committed 20 loan example of SPEC 5.9: its expected results per row, from the oracle."""

from __future__ import annotations

from dataclasses import asdict

from onepledge_datagen.application.view import DataSetView
from onepledge_datagen.domain.oracle.checks import CheckSettings
from onepledge_datagen.domain.oracle.model import Issue, RowObservation

ACCEPTED_FOR_CHECKS = ("ACCEPTED_NEW", "ACCEPTED_REPRESENTED")


def _issue(issue: Issue) -> dict[str, str]:
    return {"field": issue.field, "problem": issue.problem.value, "action": issue.action.value}


def _row(observation: RowObservation) -> dict[str, object]:
    accepted = observation.disposition.value in ACCEPTED_FOR_CHECKS
    return {
        "row_number": observation.row_number,
        "disposition": observation.disposition.value,
        "cleaning_issues": [_issue(issue) for issue in sorted(observation.issues, key=Issue.sort_key)],
        "checks": [
            {"check_type": c.check_type.value, "result": c.outcome.value, "reason_code": c.reason.value}
            for c in observation.checks
        ]
        if accepted
        else [],
        "local_state": observation.local_state.value if accepted and observation.local_state else None,
    }


def expected_document(
    view: DataSetView, observations: dict[tuple[str, int], RowObservation]
) -> dict[str, object]:
    """Per row: disposition, cleaning issues, and for accepted rows the four checks and the local state."""
    step = view.steps[0]
    facility = view.facilities[step.facility_code]
    rows = [observations[(step.step_id, number)] for number in range(1, step.data_rows + 1)]
    return {
        "description": (
            "Expected bank agent results for example_tape_20.csv after intake, cleaning and local checks "
            "against example_outside_records, with the default check settings."
        ),
        "tape": "example_tape_20.csv",
        "facility_code": step.facility_code,
        "lender_legal_name": facility.lender_legal_name,
        "kind": step.kind,
        "as_of_date": step.as_of.isoformat(),
        "check_settings": asdict(CheckSettings()),
        "rows": [_row(observation) for observation in rows],
    }
