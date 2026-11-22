"""Applying a stored registry answer to loans and alerts (SPEC 13.5, 14.5), inside the caller's transaction.

| Kind | Result | Loan | Also |
|---|---|---|---|
| BACK_BOOK, PLEDGE | LOCKED | PENDING to LOCKED (CONFLICT to LOCKED for a resubmission) | audit |
| BACK_BOOK, PLEDGE | CONFLICT | PENDING to CONFLICT | the CLAIMANT alert for the case, once |
| RELEASE | RELEASED | LOCKED to RELEASED | audit |
| any | REJECTED or unknown | unchanged | `REGISTRY_ITEM_REJECTED` with the reason |

A result whose loan is not in the state the table starts from (a replayed decision for a
car a later tape already locked) is stored and changes nothing; it is logged.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime

from onepledge_bank_agent.application.alert_maker import AlertMaker, AlertSource, CaseOnLoan
from onepledge_bank_agent.application.audit_entries import loan_state_changed
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.evidence_ports import ItemRecord
from onepledge_bank_agent.application.ports import UnitOfWork
from onepledge_bank_agent.application.registry_audit import item_rejected
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.alert import MatchFacts
from onepledge_bank_agent.domain.entities import RegistryRequest
from onepledge_bank_agent.domain.enums import AlertRole, RegistryItemResult
from onepledge_bank_agent.domain.loan import Loan, StateChange
from onepledge_bank_agent.domain.registry_records import ItemOutcome, RegistryAnswer
from onepledge_bank_agent.domain.registry_results import LoanMove, StoredResult, loan_move, stored_result

REASON_PREFIX = "REGISTRY_"


class ResultApplier:
    """Stores every item result of an answer and applies it to the loan and its alerts."""

    def __init__(self, alerts: AlertMaker, runtime: Runtime) -> None:
        self._alerts = alerts
        self._clock = runtime.clock
        self._log = runtime.log

    def apply(
        self, unit: UnitOfWork, request: RegistryRequest, items: Sequence[ItemRecord], answer: RegistryAnswer
    ) -> Counter[str]:
        """Apply the answer (already checked against what was sent); returns the count per stored result."""
        by_ref = {item.item_ref: item for item in items}
        loans = unit.loans.get_many((item.loan_id for item in items), for_update=True)
        now = self._clock.now()
        counts: Counter[str] = Counter()
        for outcome in answer.results:
            loan = loans[by_ref[outcome.item_ref].loan_id]
            stored = stored_result(request.kind, outcome)
            unit.requests.store_result(request.request_id, outcome, stored)
            counts[stored.result.value] += 1
            self._move(unit, request, loan, stored, now)
            if stored.result is RegistryItemResult.CONFLICT:
                self._claimant_alert(unit, loan, outcome)
            elif stored.result is RegistryItemResult.REJECTED:
                unit.record(item_rejected(request.request_id, loan.loan_id, stored.reason or ""))
        return counts

    def _move(
        self, unit: UnitOfWork, request: RegistryRequest, loan: Loan, stored: StoredResult, now: datetime
    ) -> None:
        move = loan_move(request.origin, stored.result, loan.state)
        transitions = {
            LoanMove.LOCK: loan.lock,
            LoanMove.LOCK_AFTER_CLEARANCE: loan.lock_after_clearance,
            LoanMove.MARK_CONFLICT: loan.mark_conflict,
            LoanMove.RELEASE: loan.release,
        }
        transition = transitions.get(move)
        if transition is None:
            if stored.result is not RegistryItemResult.REJECTED:
                self._log.warning(
                    LogEvent.REGISTRY_RESULT_NOT_APPLIED,
                    loan_id=str(loan.loan_id),
                    registry_request_id=str(request.request_id),
                    result=stored.result.value,
                    status=loan.state.value,
                )
            return
        change: StateChange = transition(now)
        unit.loans.save(loan)
        reason = REASON_PREFIX + stored.result.value
        unit.record(loan_state_changed(change, request.submission_id, reason))

    def _claimant_alert(self, unit: UnitOfWork, loan: Loan, outcome: ItemOutcome) -> None:
        if outcome.case_id is None or outcome.match_kind is None or outcome.match_basis is None:
            return
        match = MatchFacts(AlertRole.CLAIMANT, outcome.match_kind, outcome.match_basis, outcome.kinds_matched)
        case = CaseOnLoan(outcome.case_id, loan.loan_id, match)
        self._alerts.raise_once(unit, case, AlertSource.SUBMISSION_RESPONSE)
