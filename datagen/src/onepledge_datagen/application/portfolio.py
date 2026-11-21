"""Honest baseline loans of every step (SPEC 5.6)."""

from __future__ import annotations

from collections.abc import Mapping

from onepledge_datagen.application.kit import PlantingKit


class PortfolioBuilder:
    """Adds the honest baseline loans of each step with full outside records."""

    def __init__(self, baseline_counts: dict[str, int], whole_dollar_counts: Mapping[str, int]) -> None:
        self._counts = baseline_counts
        self._whole_dollars = whole_dollar_counts

    def build(self, kit: PlantingKit) -> None:
        """Baseline loans first appear on their step and pass all four local checks there.

        The first `whole_dollar_counts[step]` loans of a step get an amount without cents,
        for the whole dollar spellings of M_AMOUNT_ALT_FORMAT.
        """
        workspace = kit.workspace
        for step_id in workspace.step_order:
            bank = workspace.step(step_id).bank_code
            whole = self._whole_dollars.get(step_id, 0)
            for index in range(self._counts.get(step_id, 0)):
                loan = kit.honest_loan(step_id, whole_dollars=index < whole)
                kit.plan_records(loan, bank)
                workspace.baseline_rows[step_id].append(kit.loan_row(step_id, loan))
