"""`CreateReleaseInstruction`: release locked loans by id, without a lender file (SPEC 14.6).

Every loan id must belong to the facility and be LOCKED, else 409 LOAN_NOT_LOCKED naming
them. The canonical JSON `{"as_of_date", "facility_id", "loan_ids": sorted}` stands as
the file: its SHA-256 is the file hash, so an identical instruction is refused as a
duplicate, and each loan id is one raw row of the fixed shape of SPEC_DECISIONS 25 rule
5. The submission is stored exactly like an upload and runs through the same pipeline.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from onepledge_bank_agent.application.authorisation import Authoriser
from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.requester import Requester
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.access_policy import Operation
from onepledge_bank_agent.domain.audit import AuditDraft
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    AuditObjectType,
    AuditOutcome,
    LoanState,
    TapeKind,
    TapeSource,
)
from onepledge_bank_agent.domain.errors import (
    ConcurrentUploadError,
    DomainError,
    ErrorDetail,
    FacilityNotFoundError,
    LoanNotLockedError,
    TapeDuplicateError,
    ValidationFailedError,
)
from onepledge_bank_agent.domain.raw_evidence import (
    INSTRUCTION_FILE_NAME,
    ReleaseInstruction,
    release_instruction,
)
from onepledge_bank_agent.domain.tape import TapeFacts, TapeSubmission

EARLIEST_AS_OF_DATE = date(2000, 1, 1)
MAX_LOANS_PER_INSTRUCTION = 500


@dataclass(frozen=True)
class ReleaseCommand:
    """A release instruction: the facility, the as-of date, the loans and who asks."""

    facility_id: uuid.UUID
    as_of_date: date
    loan_ids: tuple[uuid.UUID, ...]
    requester: Requester


class CreateReleaseInstruction:
    """Stores a release instruction as a RECEIVED RELEASE submission."""

    def __init__(self, units: UnitOfWorkFactory, authoriser: Authoriser, runtime: Runtime) -> None:
        self._units = units
        self._authoriser = authoriser
        self._clock = runtime.clock
        self._ids = runtime.ids

    def execute(self, command: ReleaseCommand) -> TapeSubmission:
        """The new submission, or the named error of the first check that fails (audited)."""
        user_id = self._authoriser.require(command.requester, Operation.CREATE_RELEASE)
        try:
            self._validate(command)
            return self._store(command, user_id)
        except DomainError as error:
            self._refused(command, error)
            raise

    def _validate(self, command: ReleaseCommand) -> None:
        count = len(command.loan_ids)
        if not 1 <= count <= MAX_LOANS_PER_INSTRUCTION:
            raise ValidationFailedError((ErrorDetail("loan_ids", "OUT_OF_RANGE"),))
        if len(set(command.loan_ids)) != count:
            raise ValidationFailedError((ErrorDetail("loan_ids", "DUPLICATE"),))
        if not EARLIEST_AS_OF_DATE <= command.as_of_date <= self._clock.now().date():
            raise ValidationFailedError((ErrorDetail("as_of_date", "OUT_OF_RANGE"),))

    def _store(self, command: ReleaseCommand, user_id: uuid.UUID) -> TapeSubmission:
        instruction = release_instruction(command.as_of_date, command.facility_id, command.loan_ids)
        try:
            with self._units() as unit:
                self._check_loans(unit, command)
                existing = unit.tapes.find_by_file(
                    command.facility_id, TapeKind.RELEASE, instruction.file_sha256
                )
                if existing is not None:
                    raise TapeDuplicateError(existing)
                tape = TapeSubmission.receive(self._facts(command, instruction, user_id))
                unit.tapes.add(tape, instruction.layout)
                unit.raw_rows.add_many(tape.submission_id, instruction.rows, tape.facts.uploaded_at)
                unit.record(self._requested(command, tape))
                unit.commit()
        except ConcurrentUploadError:
            with self._units() as unit:
                found = unit.tapes.find_by_file(
                    command.facility_id, TapeKind.RELEASE, instruction.file_sha256
                )
            if found is not None:
                raise TapeDuplicateError(found) from None
            raise
        return tape

    @staticmethod
    def _check_loans(unit: UnitOfWork, command: ReleaseCommand) -> None:
        if unit.facilities.get(command.facility_id) is None:
            raise FacilityNotFoundError
        loans = unit.loans.get_many(command.loan_ids)
        refused = tuple(
            loan_id
            for loan_id in command.loan_ids
            if (loan := loans.get(loan_id)) is None
            or loan.record.facility_id != command.facility_id
            or loan.state is not LoanState.LOCKED
        )
        if refused:
            raise LoanNotLockedError(refused)

    def _facts(
        self, command: ReleaseCommand, instruction: ReleaseInstruction, user_id: uuid.UUID
    ) -> TapeFacts:
        return TapeFacts(
            submission_id=self._ids.new_id(),
            facility_id=command.facility_id,
            kind=TapeKind.RELEASE,
            source=TapeSource.API_INSTRUCTION,
            file_name=INSTRUCTION_FILE_NAME,
            file_sha256=instruction.file_sha256,
            rows_sha256=instruction.rows_sha256,
            as_of_date=command.as_of_date,
            row_count=len(instruction.rows),
            uploaded_by=user_id,
            uploaded_at=self._clock.now(),
        )

    @staticmethod
    def _requested(command: ReleaseCommand, tape: TapeSubmission) -> AuditDraft:
        return AuditDraft(
            AuditAction.RELEASE_REQUESTED,
            command.requester.actor,
            AuditObjectType.TAPE_SUBMISSION,
            tape.submission_id,
            AuditOutcome.SUCCESS,
            {
                "kind": TapeKind.RELEASE.value,
                "source": TapeSource.API_INSTRUCTION.value,
                "row_count": tape.facts.row_count,
                "file_sha256": tape.facts.file_sha256,
            },
            command.requester.request_id,
        )

    def _refused(self, command: ReleaseCommand, error: DomainError) -> None:
        with self._units() as unit:
            unit.record(
                AuditDraft(
                    AuditAction.TAPE_UPLOAD_REJECTED,
                    command.requester.actor,
                    AuditObjectType.TAPE_SUBMISSION,
                    None,
                    AuditOutcome.FAILURE,
                    {
                        "error_code": error.code,
                        "kind": TapeKind.RELEASE.value,
                        "loans": len(command.loan_ids),
                    },
                    command.requester.request_id,
                )
            )
            unit.commit()
