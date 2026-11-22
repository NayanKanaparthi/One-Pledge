"""Three bank agents sharing one registry, one pair of key holders and one outside records service.

`ThreeBanks.replay` feeds every step of a data set's manifest, in order, through each
bank's own `IngestTape` and worker; the steps of a `concurrent_group` are uploaded
together and their workers run at the same time on two threads. Nothing here reads
the answer key.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Self

from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase
from bank_testkit.doubles import BANKS, InProcessDoubles
from bank_testkit.outside import OutsideService
from bank_testkit.pipeline import BankAgentHarness, Services
from onepledge_bank_agent.application.faults import FaultInjector
from onepledge_bank_agent.domain.enums import Role, TapeKind


@dataclass(frozen=True)
class Step:
    """One manifest step."""

    step_id: str
    bank_code: str
    facility_code: str
    kind: TapeKind
    as_of: date
    file: Path
    role: Role
    group: str | None


def manifest_steps(data: Path) -> list[Step]:
    """The steps of `manifest.json` in their order."""
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    return [
        Step(
            step["step_id"],
            step["bank_code"],
            step["facility_code"],
            TapeKind(step["kind"]),
            date.fromisoformat(step["as_of_date"]),
            data / step["file"],
            Role(step["uploader_role"]),
            step["concurrent_group"],
        )
        for step in manifest["steps"]
    ]


def _batches(steps: Sequence[Step]) -> Iterator[list[Step]]:
    """Consecutive steps of one concurrent group together, every other step alone."""
    batch: list[Step] = []
    for step in steps:
        if batch and (step.group is None or step.group != batch[0].group):
            yield batch
            batch = []
        batch.append(step)
    if batch:
        yield batch


class ThreeBanks:
    """A seeded database and a bank agent for each of HARBOR, NORTHGATE and CALDER."""

    def __init__(self, new_database: Callable[[], BankDatabase], data: Path, outside: OutsideService) -> None:
        self.data = data
        self._outside = outside
        self.doubles = InProcessDoubles()
        self.databases: dict[str, BankDatabase] = {}
        self._stack = ExitStack()
        self.agents: dict[str, BankAgentHarness] = {}
        for code in BANKS:
            database = new_database()
            seed_reference(database, data, code)
            self.databases[code] = database
            harness = BankAgentHarness(
                database, Services(outside, self.doubles), environment={"BANK_CODE": code}
            )
            self.agents[code] = self._stack.enter_context(harness)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._stack.close()
        self.doubles.close()

    def replay(self) -> None:
        """Every manifest step in order, then a registry sync at every bank."""
        for batch in _batches(manifest_steps(self.data)):
            for step in batch:
                self.agents[step.bank_code].ingest(
                    step.file, step.facility_code, step.kind, step.as_of, step.role
                )
            self._run_together([step.bank_code for step in batch])
        self.sync_all()

    def agent_with(self, code: str, faults: FaultInjector) -> BankAgentHarness:
        """A second bank agent process for one bank, with a fault injector (use as a context manager)."""
        database = self.databases[code]
        services = Services(self._outside, self.doubles)
        return BankAgentHarness(database, services, faults=faults, environment={"BANK_CODE": code})

    def sync_all(self) -> None:
        """Pull every pending registry message at every bank."""
        for agent in self.agents.values():
            agent.sync()

    def _run_together(self, codes: list[str]) -> None:
        if len(codes) == 1:
            self.agents[codes[0]].run_worker()
            return
        barrier = threading.Barrier(len(codes))
        errors: list[BaseException] = []

        def work(code: str) -> None:
            try:
                barrier.wait()
                self.agents[code].run_worker()
            except BaseException as error:  # the main thread re-raises it
                errors.append(error)

        threads = [threading.Thread(target=work, args=(code,)) for code in codes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if errors:
            raise errors[0]
