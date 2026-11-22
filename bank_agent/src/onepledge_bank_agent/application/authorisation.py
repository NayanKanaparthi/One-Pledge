"""Role checks inside the use cases that branch on role (SPEC 8.6, 16.3).

`AccessPolicy` decides. A refusal writes `ACCESS_DENIED` (method, route template and
`operationId`, never a path) in its own transaction and raises `ForbiddenError`.
"""

from __future__ import annotations

import uuid

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import OperationalLog, UnitOfWorkFactory
from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.domain.access_policy import AccessPolicy, Operation
from onepledge_bank_agent.domain.audit import AuditDraft
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, AuditOutcome
from onepledge_bank_agent.domain.errors import ForbiddenError


class Authoriser:
    """Lets a request through when its role may perform the operation; audits and refuses otherwise."""

    def __init__(self, units: UnitOfWorkFactory, policy: AccessPolicy, log: OperationalLog) -> None:
        self._units = units
        self._policy = policy
        self._log = log

    def require(self, requester: Requester, operation: Operation) -> uuid.UUID:
        """The acting user's id when the role is allowed; raises `ForbiddenError` after auditing otherwise."""
        actor = requester.actor
        if (
            actor.role is not None
            and actor.user_id is not None
            and self._policy.allows(actor.role, operation)
        ):
            return actor.user_id
        route = self._policy.route(operation)
        with self._units() as unit:
            unit.record(
                AuditDraft(
                    AuditAction.ACCESS_DENIED,
                    actor,
                    AuditObjectType.ROUTE,
                    None,
                    AuditOutcome.DENIED,
                    {"method": route.method, "route": route.template, "operation": route.operation_id},
                    requester.request_id,
                )
            )
            unit.commit()
        self._log.warning(
            LogEvent.ACCESS_DENIED,
            method=route.method,
            route=route.template,
            role=actor.role.value if actor.role is not None else None,
            user_id=str(actor.user_id) if actor.user_id is not None else None,
        )
        raise ForbiddenError
