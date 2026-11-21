"""Lays out the timeline: shuffled rows, totals rows, row numbers and the manifest (SPEC 5.3, 5.6)."""

from __future__ import annotations

from onepledge_datagen.application.messiness import TOTALS_KIND, TOTALS_PROBLEM, MessinessResult
from onepledge_datagen.application.workspace import PlantedMessiness, Workspace
from onepledge_datagen.domain.entities import TapeKind
from onepledge_datagen.domain.tape_format import balance_cents


class TimelineLayout:
    """Shuffles each tape, appends Summit totals rows and numbers the data rows from 1."""

    def __init__(self, totals_rows: bool) -> None:
        self._totals_rows = totals_rows

    def lay_out(self, workspace: Workspace, messiness: MessinessResult) -> None:
        """Assign row numbers; record the totals rows and the later row of each duplicate pair."""
        for step_id in workspace.step_order:
            step = workspace.step(step_id)
            rows = workspace.streams.stream(f"shuffle:{step_id}").shuffled(workspace.rows[step_id])
            fmt = workspace.lender_format(step_id)
            if self._totals_rows and fmt.totals_row and step.kind != TapeKind.RELEASE and rows:
                total = sum(balance_cents(row.cells) for row in rows)
                totals = workspace.add_row(step_id, fmt.totals_cells(total), None)
                workspace.rows[step_id].pop()
                rows.append(totals)
                workspace.messiness.append(PlantedMessiness(TOTALS_KIND, totals, "REJECTED", TOTALS_PROBLEM))
            for number, row in enumerate(rows, start=1):
                row.row_number = number
            workspace.rows[step_id] = rows
        for pair in messiness.duplicates:
            later = pair.second if pair.second.row_number > pair.first.row_number else pair.first
            workspace.messiness.append(
                PlantedMessiness("M_DUPLICATE_ROW", later, "REJECTED", "DUPLICATE_VIN_IN_FILE")
            )

    @staticmethod
    def manifest_steps(workspace: Workspace) -> list[dict[str, object]]:
        """The `steps` list of manifest.json, with no hint of planted rows."""
        steps: list[dict[str, object]] = []
        for step_id in workspace.step_order:
            step = workspace.step(step_id)
            steps.append(
                {
                    "step_id": step.step_id,
                    "scene": step.scene,
                    "bank_code": step.bank_code,
                    "facility_code": step.facility_code,
                    "kind": step.kind.value,
                    "as_of_date": step.as_of.isoformat(),
                    "file": step.relative_path(),
                    "uploader_role": step.uploader_role,
                    "concurrent_group": step.concurrent_group,
                    "data_rows": len(workspace.rows[step_id]),
                }
            )
        return steps
