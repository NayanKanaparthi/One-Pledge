"""The three outside records lookup ports over dictionaries, for unit tests (no network)."""

from __future__ import annotations

from datetime import date

from onepledge_bank_agent.domain.errors import OutsideRecordsUnavailableError, PipelineStageError
from onepledge_bank_agent.domain.outside_records import (
    ContractRecord,
    PaymentRecord,
    PaymentWindow,
    TitleRecord,
)
from onepledge_bank_agent.domain.vin import Vin


class MemoryOutsideRecords:
    """The three lookup ports over dictionaries.

    `failures` are raised by the next calls, one each; `down_after` makes every call after
    that many successful ones raise `failure_after`, until it is set back to None.
    """

    def __init__(self) -> None:
        self.titles: dict[str, TitleRecord] = {}
        self.payment_records: dict[str, list[PaymentRecord]] = {}
        self.contract_records: dict[str, list[ContractRecord]] = {}
        self.failures: list[PipelineStageError] = []
        self.down_after: int | None = None
        self.failure_after: PipelineStageError = OutsideRecordsUnavailableError()
        self.calls = 0

    def _maybe_fail(self) -> None:
        if self.failures:
            raise self.failures.pop(0)
        if self.down_after is not None and self.calls >= self.down_after:
            raise self.failure_after
        self.calls += 1

    def title(self, vin: Vin) -> TitleRecord | None:
        """The title, or None."""
        self._maybe_fail()
        return self.titles.get(vin.value)

    def payments(self, vin: Vin, window_from: date, window_to: date) -> PaymentWindow:
        """Payments in the inclusive window, newest first, as the service filters them."""
        self._maybe_fail()
        found = [p for p in self.payment_records.get(vin.value, []) if window_from <= p.paid_on <= window_to]
        found.sort(key=lambda p: (p.paid_on, str(p.payment_id)), reverse=True)
        return PaymentWindow(window_from, window_to, tuple(found), len(found))

    def contracts(self, vin: Vin) -> tuple[ContractRecord, ...]:
        """Every contract of the vehicle."""
        self._maybe_fail()
        return tuple(self.contract_records.get(vin.value, []))
