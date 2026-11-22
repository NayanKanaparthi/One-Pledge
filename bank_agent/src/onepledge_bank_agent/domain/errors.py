"""Named business errors of the bank agent and the one table of fixed messages (SPEC 4, 18.3).

Every error derives from `DomainError` and carries a `code`. No error carries a raw
loan value: arguments are enum members, identifiers, counts or field names only.
The interface layer turns a code into an HTTP status and the fixed sentence below.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from onepledge_bank_agent.domain.enums import DependencyRefusal, FailureCode

MESSAGES: Mapping[str, str] = MappingProxyType(
    {
        "INVALID_CREDENTIALS": "The email or password is incorrect.",
        "LOGIN_RATE_LIMITED": "Too many failed sign-in attempts. Wait before trying again.",
        "UNAUTHENTICATED": "A valid access token is required.",
        "FORBIDDEN": "Your role does not allow this action.",
        "VALIDATION_ERROR": "The request is not valid. See details.",
        "NOT_FOUND": "The requested resource does not exist.",
        "METHOD_NOT_ALLOWED": "This method is not allowed on this resource.",
        "FACILITY_NOT_FOUND": "The facility does not exist.",
        "TAPE_DUPLICATE": "This file was already uploaded for this facility and kind.",
        "TAPE_TOO_LARGE": "The file exceeds the limit on size, rows, columns or cell length.",
        "TAPE_NOT_UTF8": "The file is not valid UTF-8 text.",
        "TAPE_EMPTY": "The file has no header row or no data rows.",
        "TAPE_HEADER_INVALID": "The file header is missing a required column or has a duplicate column.",
        "TAPE_MALFORMED": "The file is not a well formed comma separated file.",
        "TAPE_NOT_RETRYABLE": "This tape cannot be retried in its current state.",
        "LOAN_NOT_LOCKED": "One or more loans are not locked by this bank in this facility.",
        "LOAN_NOT_RESUBMITTABLE": "This loan can be resubmitted only when it is in conflict and every alert "
        "on it is cleared.",
        "ALERT_TRANSITION_INVALID": "This review action is not allowed in the alert's current status.",
        "POSITION_ALREADY_RECORDED": "A different position was already recorded for this alert.",
        "USER_EMAIL_TAKEN": "A user with this email already exists.",
        "LAST_ADMIN": "The last active administrator cannot be deactivated or demoted.",
        "DEPENDENCY_UNAVAILABLE": "A service this action depends on is not reachable. Nothing was changed. "
        "Try again.",
        "NOT_READY": "The service is not ready yet. Try again shortly.",
        "INTERNAL_ERROR": "An unexpected error occurred. Quote the request id when reporting it.",
    }
)


@dataclass(frozen=True)
class ErrorDetail:
    """A field name and an issue code (or an identifier). Never a submitted value."""

    field: str
    issue: str


class DomainError(Exception):
    """Base of every business error of the bank agent."""

    code = "INTERNAL_ERROR"

    def __init__(self, details: tuple[ErrorDetail, ...] = ()) -> None:
        super().__init__(self.code)
        self.details = details

    @property
    def message(self) -> str:
        """The fixed sentence of this code; internal codes read as an internal error."""
        return MESSAGES.get(self.code, MESSAGES["INTERNAL_ERROR"])


class IllegalTransitionError(DomainError):
    """A state machine was asked for a transition its table does not contain."""

    code = "ILLEGAL_TRANSITION"

    def __init__(self, entity: str, from_state: str | None, to_state: str) -> None:
        super().__init__()
        self.entity = entity
        self.from_state = from_state
        self.to_state = to_state


class AlertTransitionInvalidError(IllegalTransitionError):
    """A review action that the alert's status does not allow (SPEC 14.5)."""

    code = "ALERT_TRANSITION_INVALID"


class PositionAlreadyRecordedError(DomainError):
    """A different bank position was already recorded on the alert."""

    code = "POSITION_ALREADY_RECORDED"


class InvalidValueError(DomainError):
    """A value object was given a value outside its invariants. Names the field, never the value.

    `issue` is one code of the shared vocabulary (SPEC_DECISIONS 22, finding 7).
    """

    code = "INVALID_VALUE"

    def __init__(self, field: str, issue: str) -> None:
        super().__init__((ErrorDetail(field, issue),))
        self.field = field
        self.issue = issue


class NonCanonicalValueError(InvalidValueError, TypeError):
    """A value canonical JSON cannot hold: a float, or an object key that is not a string (SPEC 4).

    It is a `TypeError`, as SPEC 4 requires, and a `DomainError` like every business error.
    """


class InvalidVinError(InvalidValueError):
    """A VIN of the wrong length, with a character outside the alphabet, or a bad check digit.

    `reason` is the precise defect (`LENGTH`, `CHARACTER`, `CHECK_DIGIT`) the cleaning rules
    use; the detail issue is the shared vocabulary of SPEC_DECISIONS 22: a wrong length or
    character does not match the VIN pattern, a wrong check digit is an invalid value.
    """

    def __init__(self, reason: str) -> None:
        super().__init__("vin", "INVALID_VALUE" if reason == "CHECK_DIGIT" else "PATTERN_MISMATCH")
        self.reason = reason


class ValidationFailedError(DomainError):
    """A request parameter does not satisfy the contract."""

    code = "VALIDATION_ERROR"


class ForbiddenError(DomainError):
    """The acting role is not allowed to perform the operation (SPEC 16.3)."""

    code = "FORBIDDEN"


class NotFoundError(DomainError):
    """An identifier of the right shape names nothing."""

    code = "NOT_FOUND"


class FacilityNotFoundError(NotFoundError):
    """The facility named by an upload does not exist."""

    code = "FACILITY_NOT_FOUND"


class TapeRejectedError(DomainError):
    """Whole file validation refused an upload (SPEC 10.1 step 2)."""


class TapeTooLargeError(TapeRejectedError):
    """Bytes, rows, columns or a cell over the limit."""

    code = "TAPE_TOO_LARGE"


class TapeNotUtf8Error(TapeRejectedError):
    """The bytes are not UTF-8."""

    code = "TAPE_NOT_UTF8"


class TapeMalformedError(TapeRejectedError):
    """A NUL character, or the strict CSV parser refused the text."""

    code = "TAPE_MALFORMED"


class TapeEmptyError(TapeRejectedError):
    """No header row, or no data row after it."""

    code = "TAPE_EMPTY"


class TapeHeaderInvalidError(TapeRejectedError):
    """An empty or duplicate header name, two names for one field, or a missing required field."""

    code = "TAPE_HEADER_INVALID"


class TapeDuplicateError(DomainError):
    """The same file was already uploaded for this facility and kind (SPEC 10.1 step 3)."""

    code = "TAPE_DUPLICATE"

    def __init__(self, existing_submission_id: uuid.UUID) -> None:
        super().__init__((ErrorDetail("submission_id", str(existing_submission_id)),))
        self.existing_submission_id = existing_submission_id


class ConcurrentUploadError(DomainError):
    """Another transaction stored the same file at the same moment; the caller looks it up again."""

    code = "TAPE_DUPLICATE"


class PipelineStageError(DomainError):
    """A stage attempt failed because of a dependency. `transient` decides whether it is retried.

    `dependency`, `http_status` and `reason` are what the `TAPE_FAILED` audit entry records
    (SPEC_DECISIONS 26): which service, the status it answered (None when it was not
    reached) and, for a refusal, why.
    """

    transient = False
    failure_code = FailureCode.INTERNAL_ERROR
    dependency: str | None = None

    def __init__(self, http_status: int | None = None, reason: DependencyRefusal | None = None) -> None:
        super().__init__()
        self.http_status = http_status
        self.reason = reason

    def audit_detail(self) -> dict[str, str | int | None]:
        """The dependency facts of the failure, for the audit entry; empty when no dependency is named."""
        if self.dependency is None:
            return {}
        detail: dict[str, str | int | None] = {"dependency": self.dependency, "http_status": self.http_status}
        if self.reason is not None:
            detail["reason"] = self.reason.value
        return detail


OUTSIDE_RECORDS = "outside_records"


class OutsideRecordsUnavailableError(PipelineStageError):
    """Outside records could not be reached or answered 429 or 5xx after the client's retries."""

    code = "OUTSIDE_RECORDS_UNAVAILABLE"
    transient = True
    failure_code = FailureCode.OUTSIDE_RECORDS_UNAVAILABLE
    dependency = OUTSIDE_RECORDS

    def __init__(self, http_status: int | None = None) -> None:
        super().__init__(http_status)


class OutsideRecordsRejectedError(PipelineStageError):
    """Outside records refused the request (401, 403, 422 or another 4xx): usually a configuration fault."""

    code = "OUTSIDE_RECORDS_REJECTED"
    failure_code = FailureCode.OUTSIDE_RECORDS_REJECTED
    dependency = OUTSIDE_RECORDS

    def __init__(self, http_status: int, reason: DependencyRefusal) -> None:
        super().__init__(http_status, reason)


class OutsideRecordsProtocolError(PipelineStageError):
    """Outside records answered with a status or a body the contract does not allow."""

    code = "OUTSIDE_RECORDS_PROTOCOL"
    failure_code = FailureCode.OUTSIDE_RECORDS_REJECTED
    dependency = OUTSIDE_RECORDS

    def __init__(self, http_status: int | None) -> None:
        super().__init__(http_status, DependencyRefusal.MALFORMED_RESPONSE)


class MissingTapeLayoutError(DomainError):
    """A stored submission has no verbatim header; the database is not in the state intake leaves."""

    code = "INTERNAL_ERROR"


class ReferenceDataError(DomainError):
    """A reference data file (lenders and facilities) is incomplete or inconsistent."""

    code = "REFERENCE_DATA_INVALID"

    def __init__(self, sentence: str) -> None:
        super().__init__()
        self.sentence = sentence
