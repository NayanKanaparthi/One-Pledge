"""A bank agent wired by its own composition root against a test database, in-process outside records
and the in-process registry and key holder doubles."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Self

from bank_testkit.database import BankDatabase, insert_user
from bank_testkit.doubles import InProcessDoubles, combined_factory
from bank_testkit.outside import OUTSIDE_BASE_URL, OutsideService
from onepledge_bank_agent.application.advance_submission import StageOutcome
from onepledge_bank_agent.application.create_release_instruction import ReleaseCommand
from onepledge_bank_agent.application.faults import FaultInjector, NoFaults
from onepledge_bank_agent.application.ingest_tape import IngestCommand
from onepledge_bank_agent.application.ports import Clock
from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.resubmit_loan import ResubmitCommand
from onepledge_bank_agent.application.retry_tape import RetryCommand
from onepledge_bank_agent.application.review_alert import ReviewAction, ReviewCommand
from onepledge_bank_agent.application.run_pipeline_worker import WorkerReport
from onepledge_bank_agent.domain.alert import Alert
from onepledge_bank_agent.domain.audit import Actor
from onepledge_bank_agent.domain.enums import BankPosition, Role, TapeKind, TapeStatus
from onepledge_bank_agent.domain.integrity import IntegrityReport
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.tape import TapeSubmission
from onepledge_bank_agent.infrastructure.clock import SystemClock
from onepledge_bank_agent.infrastructure.stop_signal import EventStopSignal
from onepledge_bank_agent.interface.composition import Collaborators, RuntimeComposition


def _no_sleep(seconds: float) -> None:
    """Retries in tests do not wait."""


@dataclass(frozen=True)
class Services:
    """The services one bank agent reaches: outside records and the registry side doubles."""

    outside: OutsideService
    doubles: InProcessDoubles


class BankAgentHarness:
    """Intake, worker and registry side of one bank, built exactly as `onepledge-bank worker` builds them.

    `BANK_CODE` in `environment` picks the bank (HARBOR by default) and so its registry member.
    """

    def __init__(
        self,
        database: BankDatabase,
        services: Services,
        *,
        faults: FaultInjector | None = None,
        clock: Clock | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.database = database
        self.doubles = services.doubles
        extra = dict(environment or {})
        self.bank_code = extra.get("BANK_CODE", "HARBOR")
        variables = {
            "BANK_CODE": self.bank_code,
            "BANK_NAME": f"{self.bank_code.title()} Bank",
            "OUTSIDE_RECORDS_URL": OUTSIDE_BASE_URL,
            "OUTSIDE_RECORDS_API_KEY": services.outside.api_key,
            "CHECK_CHUNK_LOANS": "20",
            "TOKENIZE_CHUNK_LOANS": "25",
            **services.doubles.environment(self.bank_code),
            **extra,
        }
        self.environment = database.environment(**variables)
        self.settings = database.settings(variables)
        self.collaborators = Collaborators(
            clock=clock or SystemClock(),
            faults=faults or NoFaults(),
            http_client_factory=combined_factory(services.outside.client_factory(), services.doubles),
            sleep=_no_sleep,
            stop=EventStopSignal(),
        )
        self.stop = self.collaborators.stop
        self.composition = RuntimeComposition(self.settings, self.collaborators)
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

    def ingest(
        self, file: Path | bytes, facility_code: str, kind: TapeKind, as_of: date, role: Role | None = None
    ) -> TapeSubmission:
        """Upload a tape as the analyst (a RELEASE as the credit officer), or as the given role."""
        data = file.read_bytes() if isinstance(file, Path) else file
        name = file.name if isinstance(file, Path) else "tape.csv"
        if role is None:
            role = Role.CREDIT_OFFICER if kind is TapeKind.RELEASE else Role.ANALYST
        user = self.officer if role is Role.CREDIT_OFFICER else self.analyst
        command = IngestCommand(
            self.facility_id(facility_code), kind, as_of, name, data, Actor.user(user, role)
        )
        return self.composition.ingest_tape().execute(command)

    def run_worker(self) -> WorkerReport:
        """`onepledge-bank worker --once`."""
        return self.composition.worker().run_once()

    def run_until(self, submission: TapeSubmission, status: TapeStatus) -> TapeStatus:
        """Advance one submission stage by stage until it reaches `status` or stops advancing."""
        advance = self.composition.advance_submission()
        while True:
            run = advance.advance(submission.submission_id)
            if run.status is status or run.outcome is not StageOutcome.ADVANCED:
                return run.status

    def sync(self) -> None:
        """One registry outbox sync, as the worker runs it."""
        self.composition.outbox_sync().run()

    def requester(self, role: Role = Role.CREDIT_OFFICER) -> Requester:
        """A signed in user of the role, as the HTTP layer would pass it."""
        user = self.officer if role is Role.CREDIT_OFFICER else self.analyst
        return Requester(Actor.user(user, role), f"req-{uuid.uuid4().hex[:12]}", "api")

    def retry(self, submission: TapeSubmission) -> TapeSubmission:
        """`POST /v1/tapes/{id}/retry` as the credit officer."""
        return self.composition.retry_tape().execute(RetryCommand(submission.submission_id, self.requester()))

    def release(self, facility_id: uuid.UUID, loan_ids: Sequence[uuid.UUID], as_of: date) -> TapeSubmission:
        """`POST /v1/releases` as the credit officer."""
        command = ReleaseCommand(facility_id, as_of, tuple(loan_ids), self.requester())
        return self.composition.create_release().execute(command)

    def review(
        self, alert_id: uuid.UUID, action: ReviewAction, position: BankPosition | None = None
    ) -> Alert:
        """`POST /v1/alerts/{id}/review`: START_REVIEW as the analyst, SUBMIT_POSITION as the officer."""
        role = Role.ANALYST if action is ReviewAction.START_REVIEW else Role.CREDIT_OFFICER
        command = ReviewCommand(alert_id, action, self.requester(role), position, "reviewed with the lender")
        return self.composition.review_alert().execute(command)

    def resubmit(self, loan_id: uuid.UUID) -> Loan:
        """`POST /v1/loans/{id}/resubmit` as the credit officer."""
        return self.composition.resubmit_loan().execute(ResubmitCommand(loan_id, self.requester()))

    def verify(self) -> IntegrityReport:
        """`onepledge-bank integrity verify`."""
        return self.composition.deep_verification().execute(Requester.command_line())

    def report(self) -> IntegrityReport:
        """`GET /v1/integrity`."""
        return self.composition.integrity_report().execute()
