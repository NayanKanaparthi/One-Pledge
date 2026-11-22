"""Creating an alert once per case and loan (SPEC 14.2), from a submission response or an ALERT message.

The alert is keyed by `(registry_case_id, loan_id)`: whichever source arrives first
creates it, the other finds it and changes nothing. Its explanation comes from the
`AlertExplainer`, which sees only the match facts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import UnitOfWork
from onepledge_bank_agent.application.registry_audit import entry
from onepledge_bank_agent.application.registry_ports import AlertExplainer
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.alert import Alert, AlertIdentity, MatchFacts
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, TokenKind
from onepledge_bank_agent.domain.explanation import AlertFacts


class AlertSource(StrEnum):
    """Where the bank learned of the case."""

    SUBMISSION_RESPONSE = "SUBMISSION_RESPONSE"
    OUTBOX_MESSAGE = "OUTBOX_MESSAGE"


@dataclass(frozen=True)
class CaseOnLoan:
    """A registry case as it concerns one of this bank's loans."""

    registry_case_id: uuid.UUID
    loan_id: uuid.UUID
    match: MatchFacts


def ordered_kinds(kinds: tuple[TokenKind, ...]) -> tuple[TokenKind, ...]:
    """The kinds in the fixed kind order, each once."""
    return tuple(kind for kind in TokenKind if kind in kinds)


class AlertMaker:
    """Raises the alert of a case on a loan unless it already exists."""

    def __init__(self, explainer: AlertExplainer, runtime: Runtime) -> None:
        self._explainer = explainer
        self._clock = runtime.clock
        self._ids = runtime.ids
        self._log = runtime.log

    def raise_once(self, unit: UnitOfWork, case: CaseOnLoan, source: AlertSource) -> Alert | None:
        """The new alert, or None when one exists for this case and loan (nothing changes then)."""
        if unit.alerts.find(case.registry_case_id, case.loan_id) is not None:
            return None
        given = case.match
        match = MatchFacts(
            given.role, given.match_kind, given.match_basis, ordered_kinds(given.kinds_matched)
        )
        facts = AlertFacts(
            match.role, match.match_kind, match.match_basis, match.kinds_matched, match.kinds_not_matched
        )
        explanation = self._explainer.explain(facts)
        identity = AlertIdentity(self._ids.new_id(), case.registry_case_id, case.loan_id)
        alert = Alert.raise_new(identity, match, (explanation.text, explanation.source), self._clock.now())
        unit.alerts.add(alert)
        unit.record(
            entry(
                AuditAction.ALERT_RECEIVED,
                AuditObjectType.ALERT,
                identity.alert_id,
                {
                    "registry_case_id": str(case.registry_case_id),
                    "loan_id": str(case.loan_id),
                    "role": match.role.value,
                    "match_kind": match.match_kind.value,
                    "match_basis": match.match_basis.value,
                    "source": source.value,
                },
            )
        )
        self._log.info(
            LogEvent.ALERT_CREATED,
            alert_id=str(identity.alert_id),
            loan_id=str(case.loan_id),
            role=match.role.value,
        )
        return alert
