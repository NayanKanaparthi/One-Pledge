"""`ReviewAlert`: start a review, or record the bank's position on the case (SPEC 8.5, 14.5).

START_REVIEW (analyst or credit officer) moves NEW to REVIEWING. SUBMIT_POSITION (credit
officer only, alert REVIEWING) sends the position to the registry with no transaction
open, then stores it; if the registry reports the case resolved, the outcome is applied
as a CASE_UPDATE would. A registry answer that the case is already resolved runs a sync
and returns the alert as it then stands. An outage changes nothing. The note stays in
the bank: it is never sent, logged or written to the audit log.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from onepledge_bank_agent.application.authorisation import Authoriser
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.application.registry_ports import RegistryClient
from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.application.sync_registry_outbox import SyncRegistryOutbox
from onepledge_bank_agent.domain.access_policy import Operation
from onepledge_bank_agent.domain.alert import Alert
from onepledge_bank_agent.domain.audit import AuditDraft
from onepledge_bank_agent.domain.dependency_errors import CaseAlreadyResolvedError
from onepledge_bank_agent.domain.enums import (
    AlertStatus,
    AuditAction,
    AuditObjectType,
    AuditOutcome,
    BankPosition,
    CaseStatus,
)
from onepledge_bank_agent.domain.errors import (
    AlertTransitionInvalidError,
    ErrorDetail,
    NotFoundError,
    PositionAlreadyRecordedError,
    ValidationFailedError,
)


class ReviewAction(StrEnum):
    """The two review actions."""

    START_REVIEW = "START_REVIEW"
    SUBMIT_POSITION = "SUBMIT_POSITION"


@dataclass(frozen=True)
class ReviewCommand:
    """One review request."""

    alert_id: uuid.UUID
    action: ReviewAction
    requester: Requester
    position: BankPosition | None = None
    note: str | None = None


class ReviewAlert:
    """Applies one review action to one alert."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        registry: RegistryClient,
        authoriser: Authoriser,
        sync: SyncRegistryOutbox,
        runtime: Runtime,
    ) -> None:
        self._units = units
        self._registry = registry
        self._authoriser = authoriser
        self._sync = sync
        self._clock = runtime.clock

    def execute(self, command: ReviewCommand) -> Alert:
        """The alert after the action."""
        if command.action is ReviewAction.START_REVIEW:
            user_id = self._authoriser.require(command.requester, Operation.START_ALERT_REVIEW)
            if command.position is not None:
                raise ValidationFailedError((ErrorDetail("position", "INVALID_VALUE"),))
            return self._start(command, user_id)
        user_id = self._authoriser.require(command.requester, Operation.SUBMIT_ALERT_POSITION)
        if command.position is None:
            raise ValidationFailedError((ErrorDetail("position", "REQUIRED"),))
        return self._submit(command, command.position, user_id)

    def _start(self, command: ReviewCommand, user_id: uuid.UUID) -> Alert:
        with self._units() as unit:
            alert = unit.alerts.get(command.alert_id, for_update=True)
            if alert is None:
                raise NotFoundError
            alert.start_review(user_id, self._clock.now(), command.note)
            unit.alerts.save(alert)
            unit.record(self._audit(AuditAction.ALERT_REVIEW_STARTED, command, alert))
            unit.commit()
        return alert

    def _submit(self, command: ReviewCommand, position: BankPosition, user_id: uuid.UUID) -> Alert:
        with self._units() as unit:
            alert = unit.alerts.get(command.alert_id)
        if alert is None:
            raise NotFoundError
        record = alert.record
        if alert.status is not AlertStatus.REVIEWING:
            raise AlertTransitionInvalidError("alert", alert.status.value, AlertStatus.REVIEWING.value)
        if record.review.bank_position not in (None, position):
            raise PositionAlreadyRecordedError
        try:
            outcome = self._registry.record_position(record.registry_case_id, position)
        except CaseAlreadyResolvedError:
            self._sync.run()
            return self._current(command.alert_id)
        with self._units() as unit:
            current = unit.alerts.get(command.alert_id, for_update=True)
            if current is None:
                raise NotFoundError
            current.submit_position(position, user_id, self._clock.now(), command.note)
            if outcome.status is not CaseStatus.OPEN:
                current.apply_case_status(outcome.status)
            unit.alerts.save(current)
            unit.record(self._audit(AuditAction.ALERT_POSITION_SUBMITTED, command, current))
            unit.commit()
        return current

    def _current(self, alert_id: uuid.UUID) -> Alert:
        with self._units() as unit:
            alert = unit.alerts.get(alert_id)
        if alert is None:
            raise NotFoundError
        return alert

    @staticmethod
    def _audit(action: AuditAction, command: ReviewCommand, alert: Alert) -> AuditDraft:
        record = alert.record
        return AuditDraft(
            action,
            command.requester.actor,
            AuditObjectType.ALERT,
            record.alert_id,
            AuditOutcome.SUCCESS,
            {
                "registry_case_id": str(record.registry_case_id),
                "status": record.status.value,
                "case_status": record.case_status.value,
                "bank_position": record.review.bank_position.value if record.review.bank_position else None,
                "note_recorded": record.review.note is not None,
                **command.requester.detail(),
            },
            command.requester.request_id,
        )
