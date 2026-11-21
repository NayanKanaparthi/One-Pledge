"""A bank agent wired by its own composition root against a test database and in-process outside records."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Self

from bank_testkit.database import BankDatabase, insert_user
from bank_testkit.outside import OUTSIDE_BASE_URL, InProcessOutsideRecords
from onepledge_bank_agent.application.faults import FaultInjector, NoFaults
from onepledge_bank_agent.application.ingest_tape import IngestCommand
from onepledge_bank_agent.application.ports import Clock
from onepledge_bank_agent.application.run_pipeline_worker import WorkerReport
from onepledge_bank_agent.domain.audit import Actor
from onepledge_bank_agent.domain.enums import Role, TapeKind
from onepledge_bank_agent.domain.tape import TapeSubmission
from onepledge_bank_agent.infrastructure.clock import SystemClock
from onepledge_bank_agent.interface.composition import Collaborators, Composition


def _no_sleep(seconds: float) -> None:
    """Retries in tests do not wait."""


class BankAgentHarness:
    """Intake and worker of one bank, built exactly as `onepledge-bank worker` builds them."""

    def __init__(
        self,
        database: BankDatabase,
        outside: InProcessOutsideRecords,
        *,
        faults: FaultInjector | None = None,
        clock: Clock | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.database = database
        variables = {
            "OUTSIDE_RECORDS_URL": OUTSIDE_BASE_URL,
            "OUTSIDE_RECORDS_API_KEY": outside.api_key,
            "CHECK_CHUNK_LOANS": "20",
            **dict(environment or {}),
        }
        self.environment = database.environment(**variables)
        self.settings = database.settings(variables)
        self.collaborators = Collaborators(
            clock=clock or SystemClock(),
            faults=faults or NoFaults(),
            http_client_factory=outside.client_factory(),
            sleep=_no_sleep,
        )
        self.composition = Composition(self.settings, self.collaborators)
        self.analyst = insert_user(database, Role.ANALYST)
        self.officer = insert_user(database, Role.CREDIT_OFFICER)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self.composition.__exit__(kind, error, trace)

    def facility_id(self, code: str) -> uuid.UUID:
        """The id of a seeded facility."""
        with self.database.connect("app") as connection:
            row = connection.execute("SELECT facility_id FROM facilities WHERE code = %s", (code,)).fetchone()
        assert row is not None, code
        value = row[0]
        assert isinstance(value, uuid.UUID)
        return value

    def ingest(self, file: Path | bytes, facility_code: str, kind: TapeKind, as_of: date) -> TapeSubmission:
        """Upload a tape as the analyst (a RELEASE as the credit officer)."""
        data = file.read_bytes() if isinstance(file, Path) else file
        name = file.name if isinstance(file, Path) else "tape.csv"
        role = Role.CREDIT_OFFICER if kind is TapeKind.RELEASE else Role.ANALYST
        user = self.officer if role is Role.CREDIT_OFFICER else self.analyst
        command = IngestCommand(
            self.facility_id(facility_code), kind, as_of, name, data, Actor.user(user, role)
        )
        return self.composition.ingest_tape().execute(command)

    def run_worker(self) -> WorkerReport:
        """`onepledge-bank worker --once`."""
        return self.composition.worker().run_once()
