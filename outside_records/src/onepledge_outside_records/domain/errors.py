"""Named errors of outside records, each with the code of `contracts/outside_records.yaml`.

Messages are fixed sentences; no error carries a VIN or any other request value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

MESSAGES: Mapping[str, str] = MappingProxyType(
    {
        "UNAUTHENTICATED": "A valid API key is required.",
        "VALIDATION_ERROR": "The request is not valid. See details.",
        "TITLE_NOT_FOUND": "No title record exists for the requested vehicle.",
        "CONTRACT_NOT_FOUND": "No contract record exists for the requested vehicle.",
        "SERVICE_UNAVAILABLE": "The service is temporarily unavailable. Try again.",
        "INTERNAL_ERROR": "An unexpected error occurred. Quote the request id when reporting it.",
        "NOT_FOUND": "The requested resource does not exist.",
        "METHOD_NOT_ALLOWED": "This method is not allowed on this resource.",
    }
)


@dataclass(frozen=True)
class ErrorDetail:
    """A field name and an issue code. Never a submitted value."""

    field: str
    issue: str


class DomainError(Exception):
    """Base class of every error the service reports to a caller."""

    code = "INTERNAL_ERROR"
    http_status = 500

    def __init__(self, details: tuple[ErrorDetail, ...] = ()) -> None:
        super().__init__(self.code)
        self.details = details

    @property
    def message(self) -> str:
        """The fixed sentence of this code."""
        return MESSAGES[self.code]


class UnauthenticatedError(DomainError):
    """Missing, unknown or inactive API key."""

    code = "UNAUTHENTICATED"
    http_status = 401


class ValidationFailedError(DomainError):
    """A parameter does not match the contract."""

    code = "VALIDATION_ERROR"
    http_status = 422


class InvalidVinFormatError(ValidationFailedError):
    """A path or query VIN does not match `^[A-HJ-NPR-Z0-9]{17}$`."""

    def __init__(self) -> None:
        super().__init__((ErrorDetail("vin", "PATTERN_MISMATCH"),))


class TitleNotFoundError(DomainError):
    """No title record for the vehicle."""

    code = "TITLE_NOT_FOUND"
    http_status = 404


class ContractNotFoundError(DomainError):
    """No contract record for the vehicle."""

    code = "CONTRACT_NOT_FOUND"
    http_status = 404


class ServiceUnavailableError(DomainError):
    """The database cannot be reached."""

    code = "SERVICE_UNAVAILABLE"
    http_status = 503


class ResourceNotFoundError(DomainError):
    """No route matches the path."""

    code = "NOT_FOUND"
    http_status = 404


class MethodNotAllowedError(DomainError):
    """A known path with the wrong method."""

    code = "METHOD_NOT_ALLOWED"
    http_status = 405


class OperatorError(Exception):
    """An operator command cannot run: one sentence saying what is wrong and what to do, and a code.

    The code appears in the `--json` failure document: CONFIGURATION_ERROR,
    DATABASE_UNAVAILABLE, NOT_MIGRATED, INVALID_INPUT, PORT_UNAVAILABLE or
    OPERATION_FAILED; `serve`, which prints no document, also uses UNSAFE_DATABASE_ROLE and
    STARTUP_CHECK_FAILED.
    """

    def __init__(
        self, sentence: str, code: str = "OPERATION_FAILED", cause: BaseException | None = None
    ) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.code = code
        # The exception behind the sentence, kept (not chained) so `--debug` can show its frames.
        self.cause = cause
