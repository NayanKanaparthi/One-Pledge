"""`IngestTape`: the RECEIVED stage (SPEC 10.1).

Authorises, validates the whole file, refuses a duplicate, then stores the submission
and every raw row exactly as sent in one transaction. A refused upload stores nothing
except one audit entry saying why.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.access_policy import AccessPolicy
from onepledge_bank_agent.domain.audit import Actor, AuditDraft
from onepledge_bank_agent.domain.canonical_json import sha256_hex
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    AuditObjectType,
    AuditOutcome,
    TapeKind,
    TapeSource,
)
from onepledge_bank_agent.domain.errors import (
    ConcurrentUploadError,
    DomainError,
    ErrorDetail,
    FacilityNotFoundError,
    ForbiddenError,
    TapeDuplicateError,
    ValidationFailedError,
)
from onepledge_bank_agent.domain.tape import TapeFacts, TapeSubmission
from onepledge_bank_agent.domain.tape_file import ParsedTape, TapeFileReader, sanitise_file_name

EARLIEST_AS_OF_DATE = date(2000, 1, 1)


@dataclass(frozen=True)
class IngestCommand:
    """One upload as the interface received it."""

    facility_id: uuid.UUID
    kind: TapeKind
    as_of_date: date
    file_name: str
    data: bytes
    actor: Actor
    source: TapeSource = TapeSource.FILE_UPLOAD
    request_id: str | None = None


class IngestTape:
    """Stores an upload as a RECEIVED submission, or refuses it with a named error."""

    def __init__(
        self, units: UnitOfWorkFactory, reader: TapeFileReader, policy: AccessPolicy, runtime: Runtime
    ) -> None:
        self._units = units
        self._reader = reader
        self._policy = policy
        self._clock = runtime.clock
        self._ids = runtime.ids
        self._faults = runtime.faults
        self._log = runtime.log

    def execute(self, command: IngestCommand) -> TapeSubmission:
        """Validate and store the tape; raises the error of the first check that fails."""
        user_id = self._authorise(command)
        try:
            parsed = self._validate(command)
            tape = self._store(command, parsed, user_id)
        except DomainError as error:
            self._reject(command, error)
            raise
        self._log.info(
            LogEvent.TAPE_RECEIVED,
            submission_id=str(tape.submission_id),
            kind=command.kind.value,
            rows=tape.facts.row_count,
            user_id=str(user_id),
        )
        self._faults.reached(FaultPoint.AFTER_INTAKE_COMMIT)
        return tape

    def _authorise(self, command: IngestCommand) -> uuid.UUID:
        """The uploading user's id, or an audited `ForbiddenError`."""
        operation = self._policy.upload_operation(command.kind)
        role, user_id = command.actor.role, command.actor.user_id
        if role is not None and user_id is not None and self._policy.allows(role, operation):
            return user_id
        route = self._policy.route(operation)
        with self._units() as unit:
            unit.record(
                AuditDraft(
                    AuditAction.ACCESS_DENIED,
                    command.actor,
                    AuditObjectType.ROUTE,
                    None,
                    AuditOutcome.DENIED,
                    {"method": route.method, "route": route.template, "operation": operation.value},
                    command.request_id,
                )
            )
            unit.commit()
        self._log.warning(
            LogEvent.ACCESS_DENIED,
            method=route.method,
            route=route.template,
            role=role.value if role is not None else None,
            user_id=str(user_id) if user_id is not None else None,
        )
        raise ForbiddenError

    def _validate(self, command: IngestCommand) -> ParsedTape:
        with self._units() as unit:
            if unit.facilities.get(command.facility_id) is None:
                raise FacilityNotFoundError
        parsed = self._reader.read(command.data, command.kind)
        today = self._clock.now().date()
        if not EARLIEST_AS_OF_DATE <= command.as_of_date <= today:
            raise ValidationFailedError((ErrorDetail("as_of_date", "OUT_OF_RANGE"),))
        return parsed

    def _store(self, command: IngestCommand, parsed: ParsedTape, user_id: uuid.UUID) -> TapeSubmission:
        now = self._clock.now()
        facts = TapeFacts(
            submission_id=self._ids.new_id(),
            facility_id=command.facility_id,
            kind=command.kind,
            source=command.source,
            file_name=sanitise_file_name(command.file_name),
            file_sha256=parsed.file_sha256,
            rows_sha256=parsed.rows_sha256,
            as_of_date=command.as_of_date,
            row_count=len(parsed.rows),
            uploaded_by=user_id,
            uploaded_at=now,
        )
        tape = TapeSubmission.receive(facts)
        try:
            with self._units() as unit:
                self._refuse_duplicate(
                    unit.tapes.find_by_file(facts.facility_id, facts.kind, facts.file_sha256)
                )
                unit.tapes.add(tape, parsed.layout)
                unit.raw_rows.add_many(facts.submission_id, parsed.rows, now)
                unit.record(self._uploaded(command, facts))
                unit.commit()
        except ConcurrentUploadError:
            with self._units() as unit:
                existing = unit.tapes.find_by_file(facts.facility_id, facts.kind, facts.file_sha256)
            self._refuse_duplicate(existing)
            raise
        return tape

    @staticmethod
    def _refuse_duplicate(existing: uuid.UUID | None) -> None:
        if existing is not None:
            raise TapeDuplicateError(existing)

    @staticmethod
    def _uploaded(command: IngestCommand, facts: TapeFacts) -> AuditDraft:
        return AuditDraft(
            AuditAction.TAPE_UPLOADED,
            command.actor,
            AuditObjectType.TAPE_SUBMISSION,
            facts.submission_id,
            AuditOutcome.SUCCESS,
            {
                "kind": facts.kind.value,
                "source": facts.source.value,
                "row_count": facts.row_count,
                "file_sha256": facts.file_sha256,
                "rows_sha256": facts.rows_sha256,
            },
            command.request_id,
        )

    def _reject(self, command: IngestCommand, error: DomainError) -> None:
        detail: dict[str, str | int | None] = {
            "error_code": error.code,
            "kind": command.kind.value,
            "file_sha256": sha256_hex(command.data),
        }
        if isinstance(error, TapeDuplicateError):
            detail["existing_submission_id"] = str(error.existing_submission_id)
        with self._units() as unit:
            unit.record(
                AuditDraft(
                    AuditAction.TAPE_UPLOAD_REJECTED,
                    command.actor,
                    AuditObjectType.TAPE_SUBMISSION,
                    None,
                    AuditOutcome.FAILURE,
                    detail,
                    command.request_id,
                )
            )
            unit.commit()
        user_id = command.actor.user_id
        self._log.warning(
            LogEvent.TAPE_UPLOAD_REJECTED,
            kind=command.kind.value,
            error_code=error.code,
            user_id=str(user_id) if user_id is not None else None,
        )
