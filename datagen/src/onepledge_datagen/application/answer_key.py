"""The answer key of SPEC 5.10, built from the planted cases and data quality rows."""

from __future__ import annotations

from collections.abc import Iterable

from onepledge_datagen import GENERATOR_VERSION
from onepledge_datagen.application.workspace import Expectation, TapeRow, Workspace
from onepledge_datagen.domain import catalog
from onepledge_datagen.domain.tape_format import format_for


def inherent_fixes_json(facility_codes: Iterable[str]) -> dict[str, list[str]]:
    """FIXED problems each facility's lender format always causes, by facility code (SPEC_DECISIONS 1)."""
    return {
        code: sorted(format_for(catalog.facility(code).lender_code).inherent_fixes)
        for code in sorted(set(facility_codes))
    }


def expectation_json(expected: Expectation) -> dict[str, object]:
    """The `expected` object of one case row."""
    alert = None if expected.alert is None else {"role": expected.alert[0], "match_kind": expected.alert[1]}
    return {
        "disposition": expected.disposition,
        "cleaning_problems": list(expected.cleaning_problems),
        "outcome": expected.outcome,
        "failed_checks": list(expected.failed_checks),
        "failed_check_reasons": dict(expected.failed_check_reasons),
        "match_kind": expected.match_kind,
        "match_basis": expected.match_basis,
        "kinds_matched_include": list(expected.kinds_matched_include),
        "kinds_matched_exclude": list(expected.kinds_matched_exclude),
        "alert": alert,
    }


def _row_location(workspace: Workspace, row: TapeRow) -> dict[str, object]:
    step = workspace.step(row.step_id)
    return {
        "bank_code": step.bank_code,
        "step_id": step.step_id,
        "file": step.relative_path(),
        "row_number": row.row_number,
    }


class AnswerKeyBuilder:
    """Renders the planted cases and data quality rows once row numbers are known."""

    def build(self, workspace: Workspace, seed: int, profile: str) -> dict[str, object]:
        """The complete answer key document."""
        cases = []
        for case in workspace.cases:
            rows = [
                {
                    "role": item.role,
                    **_row_location(workspace, item.row),
                    "expected": expectation_json(item.expected),
                }
                for item in case.rows
            ]
            cases.append(
                {
                    "case_id": case.case_id,
                    "case_type": case.case_type,
                    "category": case.category,
                    "rows": rows,
                    "race_group": case.race_group,
                }
            )
        messiness = [
            {
                "kind": entry.kind,
                **_row_location(workspace, entry.row),
                "expected_action": entry.expected_action,
                "expected_problem": entry.expected_problem,
            }
            for entry in sorted(workspace.messiness, key=lambda m: (m.row.step_id, m.row.row_number))
        ]
        return {
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "profile": profile,
            "inherent_fixes": inherent_fixes_json(s.facility_code for s in workspace.steps.values()),
            "cases": cases,
            "messiness": messiness,
            "baseline": {"expected": {"disposition": "ACCEPTED", "outcome": "LOCKED", "alert": None}},
        }
