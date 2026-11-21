"""Named errors of the contract test doubles (TEST DOUBLES).

Every error a double can return is one class here with a fixed `code`, the HTTP
status of the contract, and a fixed message from `MESSAGES`. No message is ever
built from request data, so an error body cannot echo a submitted value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

MESSAGES: Mapping[str, str] = MappingProxyType(
    {
        "UNAUTHENTICATED": "A valid API key is required.",
        "FORBIDDEN": "This key is not allowed to perform this action.",
        "VALIDATION_ERROR": "The request is not valid. See details.",
        "INVALID_ELEMENT": "One or more elements are not valid group elements.",
        "KEY_VERSION_UNAVAILABLE": "The requested key version is not available on this key holder.",
        "KEY_VERSION_MISMATCH": "The key version of this submission is not the current key version.",
        "IDEMPOTENCY_KEY_REUSED": "This idempotency key was already used with a different request body.",
        "SUBMISSION_NOT_FOUND": "No submission with this id exists for the caller.",
        "CERTIFICATE_NOT_FOUND": "No certificate with this id exists for the caller.",
        "CASE_NOT_FOUND": "No case with this id exists for the caller.",
        "CASE_ALREADY_RESOLVED": "This case is already resolved.",
        "POSITION_ALREADY_RECORDED": "A different position was already recorded for this case.",
        "OUTBOX_MESSAGE_NOT_FOUND": "No outbox message with this id exists for the caller.",
        "NO_CHECKPOINT": "No checkpoint has been signed yet.",
        "PAYLOAD_TOO_LARGE": "The request body is too large.",
        "RATE_LIMITED": "The daily limit for this caller is used up. Retry after the time in Retry-After.",
        "SERVICE_UNAVAILABLE": "The service is temporarily unavailable. Try again.",
        "INTERNAL_ERROR": "An unexpected error occurred. Quote the request id when reporting it.",
        "NOT_FOUND": "The requested resource does not exist.",
        "METHOD_NOT_ALLOWED": "This method is not allowed on this resource.",
        "CONFIGURATION_ERROR": "The service configuration is not valid.",
    }
)


@dataclass(frozen=True)
class ErrorDetail:
    """One validation detail: a field name or pointer and an issue code. Never a value."""

    field: str
    issue: str


class DomainError(Exception):
    """Base class of every error a test double reports to a caller."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, details: tuple[ErrorDetail, ...] = (), retry_after_seconds: int | None = None) -> None:
        super().__init__(self.code)
        self.details = details
        self.retry_after_seconds = retry_after_seconds

    @property
    def message(self) -> str:
        """The fixed sentence for this error code."""
        return MESSAGES[self.code]


class UnauthenticatedError(DomainError):
    """The API key or test control key is missing or unknown."""

    code = "UNAUTHENTICATED"
    http_status = 401


class ForbiddenError(DomainError):
    """The key is valid but not allowed to use this view."""

    code = "FORBIDDEN"
    http_status = 403


class ValidationFailedError(DomainError):
    """The request does not match its schema or a semantic rule of the contract."""

    code = "VALIDATION_ERROR"
    http_status = 422


class InvalidElementError(DomainError):
    """At least one blinded element is not a valid ristretto255 point."""

    code = "INVALID_ELEMENT"
    http_status = 422


class KeyVersionUnavailableError(DomainError):
    """A key holder was asked to use a key version it does not hold."""

    code = "KEY_VERSION_UNAVAILABLE"
    http_status = 409


class KeyVersionMismatchError(DomainError):
    """A registry submission names a key version that is not current."""

    code = "KEY_VERSION_MISMATCH"
    http_status = 409


class IdempotencyKeyReusedError(DomainError):
    """An idempotency key was sent again with a different body."""

    code = "IDEMPOTENCY_KEY_REUSED"
    http_status = 409


class SubmissionNotFoundError(DomainError):
    """The submission does not exist for this member."""

    code = "SUBMISSION_NOT_FOUND"
    http_status = 404


class CertificateNotFoundError(DomainError):
    """The certificate does not exist for this member."""

    code = "CERTIFICATE_NOT_FOUND"
    http_status = 404


class CaseNotFoundError(DomainError):
    """The case does not exist or the member is not a party to it."""

    code = "CASE_NOT_FOUND"
    http_status = 404


class CaseAlreadyResolvedError(DomainError):
    """A position was sent for a case that is no longer open."""

    code = "CASE_ALREADY_RESOLVED"
    http_status = 409


class PositionAlreadyRecordedError(DomainError):
    """A party tried to change the position it already recorded."""

    code = "POSITION_ALREADY_RECORDED"
    http_status = 409


class OutboxMessageNotFoundError(DomainError):
    """The outbox message does not exist for this member."""

    code = "OUTBOX_MESSAGE_NOT_FOUND"
    http_status = 404


class NoCheckpointError(DomainError):
    """No checkpoint exists yet."""

    code = "NO_CHECKPOINT"
    http_status = 404


class PayloadTooLargeError(DomainError):
    """The request body exceeds the size limit."""

    code = "PAYLOAD_TOO_LARGE"
    http_status = 413


class RateLimitedError(DomainError):
    """The member's daily cap is used up. Carries the Retry-After seconds."""

    code = "RATE_LIMITED"
    http_status = 429


class ServiceUnavailableError(DomainError):
    """A failure injected through the test controls, or a dependency outage."""

    code = "SERVICE_UNAVAILABLE"
    http_status = 503


class InjectedFailureError(DomainError):
    """A failure injected through `/_test/fail-next` with a chosen status."""

    def __init__(self, status: int) -> None:
        super().__init__(retry_after_seconds=1 if status == RateLimitedError.http_status else None)
        self.http_status = status
        if status == RateLimitedError.http_status:
            self.code = RateLimitedError.code
        elif status == DomainError.http_status:
            self.code = DomainError.code
        else:
            self.code = ServiceUnavailableError.code


class ResourceNotFoundError(DomainError):
    """A path that no route of the service matches."""

    code = "NOT_FOUND"
    http_status = 404


class MethodNotAllowedError(DomainError):
    """A known path called with the wrong method."""

    code = "METHOD_NOT_ALLOWED"
    http_status = 405


class ConfigurationError(DomainError):
    """A setting is missing or malformed. Carries the variable name, never its value."""

    code = "CONFIGURATION_ERROR"
    http_status = 500

    def __init__(self, variable: str, problem: str) -> None:
        super().__init__()
        self.variable = variable
        self.problem = problem

    def sentence(self) -> str:
        """One line for the operator naming the variable and what is wrong with it."""
        return f"Configuration error: {self.variable} {self.problem}."


class InvariantViolationError(DomainError):
    """Internal state of a double would break one of its own invariants (a programming error)."""

    code = "INTERNAL_ERROR"
    http_status = 500
