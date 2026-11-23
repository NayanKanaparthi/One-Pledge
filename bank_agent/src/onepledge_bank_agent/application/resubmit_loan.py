"""`ResubmitLoan`: submit a CONFLICT loan again once every case on it was cleared (SPEC 14.5).

Allowed only when the loan is CONFLICT and every one of its alerts is CLEARED. One
request (origin RESUBMIT, kind PLEDGE, one item) is created, or found by its idempotency
key, which is derived from the cleared case ids: repeating the call replays the
registry's first decision. The request is attached to the loan's latest submission,
sent, applied (LOCKED: CONFLICT to LOCKED; CONFLICT again: the loan stays CONFLICT and a
new alert is raised for the new case), and its certificate is fetched and verified. Like
the submit stage, nothing is sent while the halt rule of SPEC 15.4 holds.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from onepledge_bank_agent.application.authorisation import Authoriser
from onepledge_bank_agent.application.deep_evidence import request_digest
from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.registry_sender import RegistrySender
from onepledge_bank_agent.application.request_planning import Candidate, RequestScope, planned_request
from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.application.submit_stage import HaltRule
from onepledge_bank_agent.application.tokenize_loans import complete_version
from onepledge_bank_agent.application.verify_certificate import VerifyCertificate
from onepledge_bank_agent.domain.access_policy import Operation
from onepledge_bank_agent.domain.audit import AuditDraft
from onepledge_bank_agent.domain.enums import (
    AlertStatus,
    AuditAction,
    AuditObjectType,
    AuditOutcome,
    LoanState,
    RequestOrigin,
    TapeKind,
    TokenKind,
)
from onepledge_bank_agent.domain.errors import LoanNotResubmittableError, NotFoundError
from onepledge_bank_agent.domain.idempotency import resubmission_batch_part
from onepledge_bank_agent.domain.loan import Loan


@dataclass(frozen=True)
class ResubmitCommand:
    """Which loan, and who asks."""

    loan_id: uuid.UUID
    requester: Requester


@dataclass(frozen=True)
class ResubmitParts:
    """The collaborators of a resubmission."""

    sender: RegistrySender
    certificates: VerifyCertificate
    authoriser: Authoriser
    member_id: uuid.UUID
    halt: HaltRule


class ResubmitLoan:
    """Sends one cleared CONFLICT loan to the registry again."""

    def __init__(self, units: UnitOfWorkFactory, parts: ResubmitParts, runtime: Runtime) -> None:
        self._units = units
        self._parts = parts
        self._ids = runtime.ids

    def execute(self, command: ResubmitCommand) -> Loan:
        """The loan after the registry's decision was applied."""
        self._parts.authoriser.require(command.requester, Operation.RESUBMIT_LOAN)
        self._parts.halt.check()
        request_id = self._request_for(command.loan_id)
        request = self._parts.sender.send(request_id)
        certificate = self._parts.certificates.collect(request)
        with self._units() as unit:
            loan = unit.loans.get_many([command.loan_id]).get(command.loan_id)
            if loan is None:
                raise NotFoundError
            unit.record(
                AuditDraft(
                    AuditAction.LOAN_RESUBMITTED,
                    command.requester.actor,
                    AuditObjectType.LOAN,
                    command.loan_id,
                    AuditOutcome.SUCCESS,
                    {
                        "registry_request_id": str(request.request_id),
                        "state": loan.state.value,
                        "certificate_id": str(certificate.certificate_id),
                        "certificate_verified": certificate.verified,
                        **request_digest(unit.history, request.request_id),
                        **command.requester.detail(),
                    },
                    command.requester.request_id,
                )
            )
            unit.commit()
        return loan

    def _request_for(self, loan_id: uuid.UUID) -> uuid.UUID:
        """The resubmission request of this loan and its cleared cases, created when it does not exist."""
        with self._units() as unit:
            loan = unit.loans.get_many([loan_id], for_update=True).get(loan_id)
            if loan is None:
                raise NotFoundError
            alerts = unit.alerts.for_loan(loan_id)
            cleared = all(alert.status is AlertStatus.CLEARED for alert in alerts)
            if loan.state is not LoanState.CONFLICT or not alerts or not cleared:
                raise LoanNotResubmittableError
            candidate = self._candidate(unit, loan)
            submission_id = unit.items.latest_submission_of(loan_id) or loan.record.first_submission_id
            scope = RequestScope(
                self._parts.member_id, submission_id, RequestOrigin.RESUBMIT, TapeKind.PLEDGE
            )
            batch_part = resubmission_batch_part(alert.record.registry_case_id for alert in alerts)
            plan = planned_request(scope, [candidate], 0, batch_part, self._ids)
            existing = unit.requests.by_key(plan.request.idempotency_key.value)
            if existing is not None:
                return existing.request_id
            unit.requests.add(plan.request, plan.items)
            unit.commit()
        return plan.request.request_id

    @staticmethod
    def _candidate(unit: UnitOfWork, loan: Loan) -> Candidate:
        stored = unit.tokens.for_loans([loan.loan_id]).get(loan.loan_id, ())
        version = complete_version(stored)
        if version is None:
            raise LoanNotResubmittableError
        asset = next(t for t in stored if t.kind is TokenKind.ASSET and t.key_version == version)
        return Candidate(0, loan.loan_id, asset.value.value, version)
