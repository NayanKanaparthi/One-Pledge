"""`RegistrySender`: send one planned request and apply the answer (SPEC 13.4, 13.5).

1. With no transaction open, `POST /v1/submissions` with the stored idempotency key and
   a body built only from stored rows. Before every attempt (the client's own retries
   included) a small transaction counts it and audits `REGISTRY_REQUEST_SENT`.
3. The answer is checked against what was sent; then one transaction stores the response,
   applies every item result and audits `REGISTRY_RESPONSE_RECEIVED`.

A request that already has a stored response is never sent again. A crash after the
registry committed but before step 3 leaves the request unanswered; the next attempt
sends the same body under the same key and the registry replays its decision.
"""

from __future__ import annotations

import uuid

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.application.registry_audit import request_sent, response_received
from onepledge_bank_agent.application.registry_ports import RegistryClient
from onepledge_bank_agent.application.request_planning import submission_body
from onepledge_bank_agent.application.result_application import ResultApplier
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.dependency_errors import RegistryProtocolError
from onepledge_bank_agent.domain.entities import RegistryRequest
from onepledge_bank_agent.domain.errors import NotFoundError
from onepledge_bank_agent.domain.registry_records import RegistryAnswer, RegistrySubmission
from onepledge_bank_agent.domain.registry_results import SentItem, answer_matches


class RegistrySender:
    """Sends a planned request once it is unanswered and stores what the registry decided."""

    def __init__(
        self, units: UnitOfWorkFactory, registry: RegistryClient, applier: ResultApplier, runtime: Runtime
    ) -> None:
        self._units = units
        self._registry = registry
        self._applier = applier
        self._clock = runtime.clock
        self._faults = runtime.faults
        self._log = runtime.log

    def send(self, request_id: uuid.UUID) -> RegistryRequest:
        """Send the request if it has no stored response; returns it as stored afterwards."""
        prepared = self._prepare(request_id)
        if prepared is None:
            return self._stored(request_id)
        request, body = prepared
        answer = self._registry.submit(request.idempotency_key.value, body, lambda: self._count(request))
        self._faults.reached(FaultPoint.AFTER_REGISTRY_RESPONSE)
        sent = [SentItem(item.item_ref, item.asset_token) for item in body.items]
        if not answer_matches(sent, answer.results):
            raise RegistryProtocolError(answer.status)
        self._store(request, answer)
        self._faults.reached(FaultPoint.AFTER_RESPONSE_STORED)
        return self._stored(request_id)

    def _prepare(self, request_id: uuid.UUID) -> tuple[RegistryRequest, RegistrySubmission] | None:
        """The request and its body; None when the request is already answered."""
        with self._units() as unit:
            request = unit.requests.get(request_id)
            if request is None:
                raise NotFoundError
            if request.answered:
                return None
            items = unit.requests.items(request_id)
            tokens = unit.tokens.for_loans(item.loan_id for item in items)
            return request, submission_body(request, items, tokens)

    def _count(self, request: RegistryRequest) -> None:
        """One send: `attempts += 1`, `sent_at` once, `last_attempt_at`, audited, in its own transaction."""
        with self._units() as unit:
            attempts = unit.requests.mark_sent(request.request_id, self._clock.now())
            unit.record(request_sent(request, attempts))
            unit.commit()
        self._log.info(
            LogEvent.REGISTRY_REQUEST_SENT,
            registry_request_id=str(request.request_id),
            submission_id=str(request.submission_id),
            batch_index=request.batch_index,
            item_count=request.item_count,
            attempts=attempts,
        )

    def _store(self, request: RegistryRequest, answer: RegistryAnswer) -> None:
        with self._units() as unit:
            current = unit.requests.get(request.request_id)
            if current is None or current.answered:
                return
            items = unit.requests.items(request.request_id)
            unit.requests.store_answer(request.request_id, answer, self._clock.now())
            counts = self._applier.apply(unit, request, items, answer)
            unit.record(response_received(request, answer, counts))
            unit.commit()
        self._log.info(
            LogEvent.REGISTRY_RESPONSE_STORED,
            registry_request_id=str(request.request_id),
            submission_id=str(request.submission_id),
            http_status=answer.status,
            item_count=len(answer.results),
        )

    def _stored(self, request_id: uuid.UUID) -> RegistryRequest:
        with self._units() as unit:
            request = unit.requests.get(request_id)
        if request is None:
            raise NotFoundError
        return request
