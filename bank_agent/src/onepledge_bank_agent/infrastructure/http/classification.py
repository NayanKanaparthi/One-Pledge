"""The one classification of outbound HTTP failures, for all four dependency clients (SPEC_DECISIONS 49).

| What happened | Class | reason |
|---|---|---|
| connection refused or reset, DNS failure, any other transport error | transient | CONNECTION_FAILED |
| connect, read, write or pool timeout | transient | TIMEOUT |
| 429 | transient | RATE_LIMITED |
| 500, 502, 503, 504 (and any other 5xx) | transient | SERVER_ERROR |
| 401 | permanent | UNAUTHENTICATED |
| 403 | permanent | FORBIDDEN |
| any other 4xx the contract gives no meaning for that call | permanent | REQUEST_REFUSED |
| 3xx; undecodable; not JSON; not the contract; another object; incomplete | permanent | MALFORMED_RESPONSE |

An exception class of the HTTP library that this table does not name is MALFORMED_RESPONSE,
never an unexpected error. `DependencyErrors` names the classes for one dependency, so a
client never builds a failure any other way.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

import httpx

from onepledge_bank_agent.domain.dependency_errors import (
    KEY_HOLDER_1,
    KEY_HOLDER_2,
    REGISTRY,
    KeyHolderRejectedError,
    KeyHolderUnavailableError,
    RegistryRejectedError,
    RegistryUnavailableError,
)
from onepledge_bank_agent.domain.enums import FailureReason
from onepledge_bank_agent.domain.errors import (
    OUTSIDE_RECORDS,
    OutsideRecordsRejectedError,
    OutsideRecordsUnavailableError,
    PipelineStageError,
)


@dataclass(frozen=True)
class Classified:
    """The class of one failure: transient or permanent, and its reason."""

    transient: bool
    reason: FailureReason


STATUSES: Mapping[int, Classified] = MappingProxyType(
    {
        429: Classified(True, FailureReason.RATE_LIMITED),
        500: Classified(True, FailureReason.SERVER_ERROR),
        502: Classified(True, FailureReason.SERVER_ERROR),
        503: Classified(True, FailureReason.SERVER_ERROR),
        504: Classified(True, FailureReason.SERVER_ERROR),
        401: Classified(False, FailureReason.UNAUTHENTICATED),
        403: Classified(False, FailureReason.FORBIDDEN),
    }
)
MALFORMED = Classified(False, FailureReason.MALFORMED_RESPONSE)
CLIENT_ERRORS = range(400, 500)
SERVER_ERRORS = range(500, 600)


def classify_status(status: int) -> Classified:
    """The class of an answer that is not the success the call expects."""
    if status in STATUSES:
        return STATUSES[status]
    if status in CLIENT_ERRORS:
        return Classified(False, FailureReason.REQUEST_REFUSED)
    if status in SERVER_ERRORS:
        return Classified(True, FailureReason.SERVER_ERROR)
    return MALFORMED


def classify_exception(error: Exception) -> Classified:
    """The class of an exception raised while sending a request or reading its answer."""
    if isinstance(error, httpx.TimeoutException):
        return Classified(True, FailureReason.TIMEOUT)
    if isinstance(error, httpx.DecodingError):
        return MALFORMED
    if isinstance(error, httpx.TransportError):
        return Classified(True, FailureReason.CONNECTION_FAILED)
    return MALFORMED


@dataclass(frozen=True)
class DependencyErrors:
    """How one dependency's failures are named: a transient and a permanent class."""

    unavailable: Callable[[int | None, FailureReason], PipelineStageError]
    rejected: Callable[[int | None, FailureReason], PipelineStageError]

    def of(self, http_status: int | None, classified: Classified) -> PipelineStageError:
        """The named error of a classified failure."""
        build = self.unavailable if classified.transient else self.rejected
        return build(http_status, classified.reason)

    def malformed(self, http_status: int | None) -> PipelineStageError:
        """An answer outside the contract."""
        return self.rejected(http_status, FailureReason.MALFORMED_RESPONSE)


def _key_holder_errors(dependency: str) -> DependencyErrors:
    return DependencyErrors(
        lambda status, reason: KeyHolderUnavailableError(dependency, status, reason),
        lambda status, reason: KeyHolderRejectedError(dependency, status, reason),
    )


ERRORS: Mapping[str, DependencyErrors] = MappingProxyType(
    {
        OUTSIDE_RECORDS: DependencyErrors(OutsideRecordsUnavailableError, OutsideRecordsRejectedError),
        KEY_HOLDER_1: _key_holder_errors(KEY_HOLDER_1),
        KEY_HOLDER_2: _key_holder_errors(KEY_HOLDER_2),
        REGISTRY: DependencyErrors(RegistryUnavailableError, RegistryRejectedError),
    }
)


def failure(response: httpx.Response, errors: DependencyErrors) -> PipelineStageError:
    """The named error of an answer that is not the success the call expects."""
    return errors.of(response.status_code, classify_status(response.status_code))
