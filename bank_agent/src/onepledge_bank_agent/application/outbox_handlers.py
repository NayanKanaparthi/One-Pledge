"""The handlers of the three registry message types (SPEC 14.2, 14.4), each inside the caller's transaction.

Every handler is idempotent, because a crash between commit and acknowledgement makes
the registry deliver the message again: an ALERT creates the alert of `(case, loan)`
once; a CASE_UPDATE whose status equals the stored case status changes nothing; a
CHECKPOINT already stored is a duplicate. No handler changes a loan's state.
"""

from __future__ import annotations

from onepledge_bank_agent.application.alert_maker import AlertMaker, AlertSource, CaseOnLoan
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import OperationalLog, UnitOfWork
from onepledge_bank_agent.application.registry_audit import entry
from onepledge_bank_agent.domain.alert import MatchFacts
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    AuditObjectType,
    AuditOutcome,
    CaseStatus,
    LoanState,
)
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.registry_records import AlertNotice, CaseNotice


def own_loan(loans: list[Loan]) -> Loan | None:
    """Of the loans sharing an ASSET code, the one that is not RELEASED, else the newest (SPEC 14.2)."""
    live = [loan for loan in loans if loan.state is not LoanState.RELEASED]
    candidates = live or loans
    return max(candidates, key=lambda loan: (loan.record.created_at, str(loan.loan_id)), default=None)


class AlertMessages:
    """ALERT: find this bank's loan by its ASSET code and raise the alert once."""

    def __init__(self, alerts: AlertMaker) -> None:
        self._alerts = alerts

    def handle(self, unit: UnitOfWork, notice: AlertNotice) -> bool:
        """True when a new alert was created; an unknown code is audited as ALERT_UNMATCHED."""
        loan_ids = unit.tokens.loans_with_asset_token(notice.asset_token)
        loan = own_loan(list(unit.loans.get_many(loan_ids).values()))
        if loan is None:
            unit.record(
                entry(
                    AuditAction.ALERT_UNMATCHED,
                    AuditObjectType.ALERT,
                    None,
                    {"registry_case_id": str(notice.case_id), "role": notice.role.value},
                    AuditOutcome.FAILURE,
                )
            )
            return False
        match = MatchFacts(notice.role, notice.match_kind, notice.match_basis, notice.kinds_matched)
        case = CaseOnLoan(notice.case_id, loan.loan_id, match)
        return self._alerts.raise_once(unit, case, AlertSource.OUTBOX_MESSAGE) is not None


class CaseUpdateMessages:
    """CASE_UPDATE: record the case outcome on the alert, closing it when it is still open."""

    def __init__(self, log: OperationalLog) -> None:
        self._log = log

    def handle(self, unit: UnitOfWork, notice: CaseNotice) -> int:
        """The number of alerts that changed (0 when the status was already stored)."""
        if notice.status is CaseStatus.OPEN:
            return 0
        loan_ids = unit.tokens.loans_with_asset_token(notice.asset_token)
        changed = 0
        for alert in unit.alerts.for_case(notice.case_id, loan_ids):
            before = alert.status
            if not alert.apply_case_status(notice.status):
                continue
            unit.alerts.save(alert)
            changed += 1
            unit.record(
                entry(
                    AuditAction.ALERT_CASE_UPDATED,
                    AuditObjectType.ALERT,
                    alert.record.alert_id,
                    {
                        "registry_case_id": str(notice.case_id),
                        "case_status": notice.status.value,
                        "from": before.value,
                        "to": alert.status.value,
                        "resolved_seq": notice.resolved_seq,
                    },
                )
            )
            self._log.info(
                LogEvent.ALERT_CASE_UPDATED, alert_id=str(alert.record.alert_id), status=alert.status.value
            )
        return changed
