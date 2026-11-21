"""Honest baseline loans of every step (SPEC 5.6)."""

from __future__ import annotations

from onepledge_datagen.application.kit import PlantingKit


class PortfolioBuilder:
    """Adds the honest baseline loans of each step with full outside records."""

    def __init__(self, baseline_counts: dict[str, int]) -> None:
        self._counts = baseline_counts

    def build(self, kit: PlantingKit) -> None:
        """Baseline loans first appear on their step and pass all four local checks there."""
        workspace = kit.workspace
        for step_id in workspace.step_order:
            bank = workspace.step(step_id).bank_code
            for _ in range(self._counts.get(step_id, 0)):
                loan = kit.honest_loan(step_id)
                kit.plan_records(loan, bank)
                workspace.baseline_rows[step_id].append(kit.loan_row(step_id, loan))
