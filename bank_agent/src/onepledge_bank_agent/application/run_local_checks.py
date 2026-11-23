"""`RunLocalChecks`: the CHECKED stage (SPEC 11, 13.2).

Loans are checked in row number order, in chunks. For each chunk every outside call is
made first with no transaction open; then one transaction stores the four results per
loan, holds the loans that failed, and audits. A loan that already has four rows for
this submission is skipped, so a resumed stage never checks a loan twice. A source
outage raises before anything of the chunk is written.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from onepledge_bank_agent.application.audit_entries import loan_state_changed, stage_advanced
from onepledge_bank_agent.application.deep_evidence import stage_digests
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.ports import (
    AcceptedItem,
    ContractsClient,
    PaymentsClient,
    TitleRecordsClient,
    UnitOfWorkFactory,
)
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.checks import LocalCheckRules, failed_check_types, holds_locally
from onepledge_bank_agent.domain.enums import LoanState, TapeKind, TapeStatus
from onepledge_bank_agent.domain.errors import FacilityNotFoundError
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.outside_records import OutsideEvidence
from onepledge_bank_agent.domain.tape import TapeFacts
from onepledge_bank_agent.domain.values import CheckResult

LOCAL_CHECK_FAILED = "LOCAL_CHECK_FAILED"


@dataclass(frozen=True)
class OutsideRecordsClients:
    """The three outside records lookups the checks call."""

    titles: TitleRecordsClient
    payments: PaymentsClient
    contracts: ContractsClient


class RunLocalChecks:
    """Runs the check stage of one CLEANED submission."""

    reached = TapeStatus.CHECKED

    def __init__(
        self,
        units: UnitOfWorkFactory,
        sources: OutsideRecordsClients,
        rules: LocalCheckRules,
        runtime: Runtime,
        chunk_size: int,
    ) -> None:
        self._units = units
        self._sources = sources
        self._rules = rules
        self._clock = runtime.clock
        self._faults = runtime.faults
        self._after_unit = runtime.after_unit
        self._log = runtime.log
        self._chunk_size = chunk_size

    def run(self, submission_id: uuid.UUID) -> None:
        """Check every accepted loan of the submission, then mark it CHECKED."""
        with self._units() as unit:
            tape = unit.tapes.get(submission_id)
            if tape is None or tape.status is not TapeStatus.CLEANED:
                return
            facility = unit.facilities.get(tape.facts.facility_id)
            items = [] if tape.facts.kind is TapeKind.RELEASE else unit.items.accepted(submission_id)
        if facility is None:
            raise FacilityNotFoundError
        for index, start in enumerate(range(0, len(items), self._chunk_size)):
            chunk = items[start : start + self._chunk_size]
            self._check_chunk(tape.facts, facility.lender.legal_name, chunk, index)
        self._finish(submission_id, [item.loan_id for item in items])

    def _check_chunk(
        self, facts: TapeFacts, lender: str, chunk: Sequence[AcceptedItem], batch_index: int
    ) -> None:
        ids = [item.loan_id for item in chunk]
        with self._units() as unit:
            done = unit.checks.checked(facts.submission_id, ids)
            loans = unit.loans.get_many(loan_id for loan_id in ids if loan_id not in done)
        if not loans:
            return
        results = {loan_id: self._evaluate(loan, lender, facts) for loan_id, loan in loans.items()}
        now = self._clock.now()
        with self._units() as unit:
            done = unit.checks.checked(facts.submission_id, results)
            current = unit.loans.get_many(
                (loan_id for loan_id in results if loan_id not in done), for_update=True
            )
            for loan_id in sorted(current, key=ids.index):
                loan_results = results[loan_id]
                unit.checks.add(facts.submission_id, loan_id, loan_results, now)
                loan = current[loan_id]
                if loan.state is LoanState.PENDING and holds_locally(loan_results):
                    change = loan.hold_local(now)
                    unit.loans.save(loan)
                    failed = [check_type.value for check_type in failed_check_types(loan_results)]
                    unit.record(loan_state_changed(change, facts.submission_id, LOCAL_CHECK_FAILED, failed))
            unit.commit()
        self._log.info(
            LogEvent.CHECK_CHUNK_COMMITTED,
            submission_id=str(facts.submission_id),
            batch_index=batch_index,
            count=len(current),
        )
        self._faults.reached(FaultPoint.AFTER_CHECK_CHUNK)
        self._after_unit()

    def _evaluate(self, loan: Loan, lender: str, facts: TapeFacts) -> tuple[CheckResult, ...]:
        vin = loan.terms.vin
        window_from, window_to = self._rules.payment_window(facts.as_of_date)
        evidence = OutsideEvidence(
            title=self._sources.titles.title(vin),
            payments=self._sources.payments.payments(vin, window_from, window_to),
            contracts=self._sources.contracts.contracts(vin),
        )
        return self._rules.evaluate(loan.terms, lender, facts.as_of_date, evidence)

    def _finish(self, submission_id: uuid.UUID, loan_ids: Sequence[uuid.UUID]) -> None:
        with self._units() as unit:
            tape = unit.tapes.get(submission_id, for_update=True)
            if tape is None or tape.status is not TapeStatus.CLEANED:
                return
            loans = unit.loans.get_many(loan_ids)
            held = sum(1 for loan in loans.values() if loan.state is LoanState.HELD_LOCAL)
            before = tape.mark_checked(self._clock.now())
            unit.tapes.save(tape)
            counts = {"loans_checked": len(loan_ids), "loans_held_local": held}
            digests = stage_digests(unit.history, submission_id, tape.status)
            unit.record(stage_advanced(submission_id, before, tape.status, {**counts, **digests}))
            unit.commit()
