"""The request cycle shared by `SubmitPledge` and `ReleaseLoans` (SPEC 13.2 submit row, 13.4).

Plan once (one transaction, skipped when the submission already has its tape
requests), then send every unanswered request in batch order, then mark the submission
SUBMITTED in one transaction once every planned request has a stored response. Zero
eligible loans plan zero requests: the stage advances and nothing leaves the bank.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from onepledge_bank_agent.application.audit_entries import stage_advanced
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.registry_sender import RegistrySender
from onepledge_bank_agent.application.request_planning import (
    Candidate,
    RequestScope,
    batches,
    planned_request,
)
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.enums import RequestOrigin, TapeStatus
from onepledge_bank_agent.domain.idempotency import tape_batch_part
from onepledge_bank_agent.domain.tape import TapeSubmission


@dataclass(frozen=True)
class SubmitSettings:
    """This bank's registry member id and the items per request (`SUBMIT_BATCH_ITEMS`)."""

    member_id: uuid.UUID
    batch_items: int


class CandidateSelector(Protocol):
    """Chooses the loans of a submission that go to the registry."""

    def candidates(self, unit: UnitOfWork, tape: TapeSubmission) -> list[Candidate]:
        """The eligible loans with their ASSET codes and key versions."""
        ...


class TapeRequests:
    """Plans, sends and completes the tape requests of one TOKENIZED submission."""

    def __init__(
        self, units: UnitOfWorkFactory, sender: RegistrySender, runtime: Runtime, settings: SubmitSettings
    ) -> None:
        self._units = units
        self._sender = sender
        self._clock = runtime.clock
        self._ids = runtime.ids
        self._faults = runtime.faults
        self._log = runtime.log
        self._settings = settings

    def run(self, submission_id: uuid.UUID, selector: CandidateSelector) -> None:
        """Plan if needed, send what is unanswered, mark SUBMITTED."""
        planned = self._plan(submission_id, selector)
        if planned is None:
            return
        for request_id in planned:
            self._sender.send(request_id)
        self._finish(submission_id)

    def _plan(self, submission_id: uuid.UUID, selector: CandidateSelector) -> list[uuid.UUID] | None:
        with self._units() as unit:
            tape = unit.tapes.get(submission_id, for_update=True)
            if tape is None or tape.status is not TapeStatus.TOKENIZED:
                return None
            existing = unit.requests.for_submission(submission_id, RequestOrigin.TAPE)
            if existing:
                return [request.request_id for request in existing]
            scope = RequestScope(self._settings.member_id, submission_id, RequestOrigin.TAPE, tape.facts.kind)
            groups = batches(selector.candidates(unit, tape), self._settings.batch_items)
            plans = [
                planned_request(scope, batch, index, tape_batch_part(index), self._ids)
                for index, batch in enumerate(groups)
            ]
            for plan in plans:
                unit.requests.add(plan.request, plan.items)
            unit.commit()
        self._log.info(
            LogEvent.REGISTRY_REQUESTS_PLANNED,
            submission_id=str(submission_id),
            count=len(plans),
            item_count=sum(plan.request.item_count for plan in plans),
        )
        self._faults.reached(FaultPoint.AFTER_REQUESTS_PLANNED)
        return [plan.request.request_id for plan in plans]

    def _finish(self, submission_id: uuid.UUID) -> None:
        with self._units() as unit:
            tape = unit.tapes.get(submission_id, for_update=True)
            if tape is None or tape.status is not TapeStatus.TOKENIZED:
                return
            requests = unit.requests.for_submission(submission_id, RequestOrigin.TAPE)
            if not all(request.answered for request in requests):
                return
            results: Counter[str] = Counter()
            for request in requests:
                stored = unit.requests.items(request.request_id)
                results.update(item.result.value for item in stored if item.result is not None)
            before = tape.mark_submitted(self._clock.now())
            unit.tapes.save(tape)
            counts = {
                "requests": len(requests),
                "items_submitted": sum(request.item_count for request in requests),
                **{f"items_{name.lower()}": count for name, count in sorted(results.items())},
            }
            unit.record(stage_advanced(submission_id, before, tape.status, counts))
            unit.commit()
