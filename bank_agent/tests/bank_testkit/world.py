"""A small in-memory bank for use case tests: one store, two facilities, a runtime with test collaborators."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from bank_testkit.clocks import CountingIds, ManualClock
from bank_testkit.memory import MemoryStore, MemoryUnits
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.ports import LogValue
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.audit import AuditDraft
from onepledge_bank_agent.domain.entities import Facility, Lender
from onepledge_bank_agent.domain.enums import AuditAction, FacilityStatus
from onepledge_bank_agent.domain.values import Lei

SUMMIT_LEI = "00000ONEPLEDGESUM134"


class CrashError(BaseException):
    """Unwinds everything like a killed process; never caught by the product's `except Exception`."""


class Faults:
    """Raises `CrashError` when the pipeline reaches the armed point (after `skip` earlier passes)."""

    def __init__(self) -> None:
        self.armed: FaultPoint | None = None
        self.skip = 0
        self.passed: list[FaultPoint] = []

    def arm(self, point: FaultPoint, skip: int = 0) -> None:
        self.armed, self.skip = point, skip

    def reached(self, point: FaultPoint) -> None:
        self.passed.append(point)
        if point is self.armed:
            if self.skip:
                self.skip -= 1
                return
            self.armed = None
            raise CrashError(point.value)


@dataclass
class RecordingLog:
    """Keeps every operational log call."""

    events: list[tuple[str, str, dict[str, LogValue]]] = field(default_factory=list)

    def info(self, event: str, **fields: LogValue) -> None:
        self.events.append(("info", event, fields))

    def warning(self, event: str, **fields: LogValue) -> None:
        self.events.append(("warning", event, fields))

    def failure(self, event: str, error: BaseException, **fields: LogValue) -> None:
        self.events.append(("error", event, {**fields, "exception_type": type(error).__name__}))


class World:
    """The store, its unit of work factory, a runtime and two seeded facilities of one lender."""

    def __init__(self) -> None:
        self.store = MemoryStore()
        self.units = MemoryUnits(self.store)
        self.clock = ManualClock()
        self.ids = CountingIds(1000)
        self.faults = Faults()
        self.log = RecordingLog()
        self.runtime = Runtime(self.clock, self.ids, self.faults, self.log)
        self.lender = Lender(uuid.uuid4(), "Summit Auto Finance LLC", Lei(SUMMIT_LEI))
        self.facility = Facility(
            uuid.uuid4(), self.lender.lender_id, "HARBOR-SUMMIT", "Summit line", 7_500_000_000, 8500,
            FacilityStatus.ACTIVE, date(2025, 3, 1),
        )  # fmt: skip
        self.other_facility = Facility(
            uuid.uuid4(),
            self.lender.lender_id,
            "HARBOR-OTHER",
            "Other line",
            1,
            1,
            FacilityStatus.ACTIVE,
            date(2025, 3, 1),
        )
        with self.units() as unit:
            unit.facilities.upsert_lender(self.lender)
            unit.facilities.upsert_facility(self.facility)
            unit.facilities.upsert_facility(self.other_facility)
            unit.commit()

    def audit(self, action: AuditAction | None = None) -> list[AuditDraft]:
        """Committed audit drafts, optionally of one action."""
        return [d for d in self.store.state.audit if action is None or d.action is action]
