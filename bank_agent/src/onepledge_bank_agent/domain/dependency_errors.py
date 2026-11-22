"""Named errors of the two shared services and of the bank's checks on what they return (SPEC 9.4, 13).

Each is a `PipelineStageError`: `transient` decides whether the stage is retried with
backoff; `code` and `failure_code` are the one failure code a FAILED submission records;
`dependency`, `http_status` and `reason` are the facts of SPEC_DECISIONS 58, with
`reason` from the classification of SPEC_DECISIONS 49 or one of the bank's own findings.
"""

from __future__ import annotations

from onepledge_bank_agent.domain.enums import FailureCode, FailureReason
from onepledge_bank_agent.domain.errors import PipelineStageError

KEY_HOLDER_1 = "key_holder_1"
KEY_HOLDER_2 = "key_holder_2"
REGISTRY = "registry"


class KeyHolderUnavailableError(PipelineStageError):
    """A key holder could not be reached, timed out, or answered 429 or 5xx (a transient class)."""

    code = FailureCode.KEY_HOLDER_UNAVAILABLE.value
    transient = True
    failure_code = FailureCode.KEY_HOLDER_UNAVAILABLE
    default_reason = FailureReason.CONNECTION_FAILED

    def __init__(
        self, dependency: str, http_status: int | None = None, reason: FailureReason | None = None
    ) -> None:
        super().__init__(http_status, reason)
        self.dependency = dependency


class KeyHolderRejectedError(PipelineStageError):
    """A key holder refused the request or answered outside the contract (a permanent class)."""

    code = FailureCode.KEY_HOLDER_REJECTED.value
    failure_code = FailureCode.KEY_HOLDER_REJECTED
    default_reason = FailureReason.MALFORMED_RESPONSE

    def __init__(self, dependency: str, http_status: int | None, reason: FailureReason | None = None) -> None:
        super().__init__(http_status, reason)
        self.dependency = dependency


class KeyHolderProtocolError(KeyHolderRejectedError):
    """A key holder's elements break SPEC 9.3 step 4: a wrong count, an element that is not a valid point,
    or an unblinded result that is the identity (PROTOCOL_VIOLATION)."""

    def __init__(self, dependency: str, http_status: int | None) -> None:
        super().__init__(dependency, http_status, FailureReason.PROTOCOL_VIOLATION)


class KeyVersionChangedError(PipelineStageError):
    """A tokenise round reported a key version pair other than the one this submission's tokens use."""

    code = FailureCode.KEY_VERSION_MISMATCH.value
    failure_code = FailureCode.KEY_VERSION_MISMATCH
    default_reason = FailureReason.KEY_VERSION_MISMATCH

    def __init__(self, dependency: str) -> None:
        super().__init__()
        self.dependency = dependency


class RegistryUnavailableError(PipelineStageError):
    """The registry could not be reached, timed out, or answered 429 or 5xx (a transient class)."""

    code = FailureCode.REGISTRY_UNAVAILABLE.value
    transient = True
    failure_code = FailureCode.REGISTRY_UNAVAILABLE
    dependency = REGISTRY
    default_reason = FailureReason.CONNECTION_FAILED


class RegistryRejectedError(PipelineStageError):
    """The registry refused the request or answered outside the contract (a permanent class)."""

    code = FailureCode.REGISTRY_REJECTED_REQUEST.value
    failure_code = FailureCode.REGISTRY_REJECTED_REQUEST
    dependency = REGISTRY
    default_reason = FailureReason.MALFORMED_RESPONSE


class RegistryProtocolError(RegistryRejectedError):
    """The registry answered outside the contract, or with results that do not match what was sent."""

    def __init__(self, http_status: int | None) -> None:
        super().__init__(http_status, FailureReason.MALFORMED_RESPONSE)


class KeyVersionMismatchError(PipelineStageError):
    """The registry refused the key version of the codes (409 KEY_VERSION_MISMATCH). Not retryable."""

    code = FailureCode.KEY_VERSION_MISMATCH.value
    failure_code = FailureCode.KEY_VERSION_MISMATCH
    dependency = REGISTRY
    default_reason = FailureReason.KEY_VERSION_MISMATCH


class CaseAlreadyResolvedError(PipelineStageError):
    """The registry already resolved the case (409 CASE_ALREADY_RESOLVED); a review then syncs instead."""

    code = "CASE_ALREADY_RESOLVED"
    failure_code = FailureCode.REGISTRY_REJECTED_REQUEST
    dependency = REGISTRY
    default_reason = FailureReason.REQUEST_REFUSED


class CertificateInvalidError(PipelineStageError):
    """A certificate of the submission failed the bank's own verification (SPEC 13.6, SPEC_DECISIONS 61)."""

    code = FailureCode.CERTIFICATE_INVALID.value
    failure_code = FailureCode.CERTIFICATE_INVALID
    dependency = REGISTRY
    default_reason = FailureReason.CERTIFICATE_INVALID


class RegistryIntegrityBrokenError(PipelineStageError):
    """The halt rule is active: nothing state changing is sent to the registry (SPEC 15.4, SPEC_DECISIONS 56).

    No service was called, so `dependency` and `http_status` are None.
    """

    code = FailureCode.REGISTRY_INTEGRITY_BROKEN.value
    failure_code = FailureCode.REGISTRY_INTEGRITY_BROKEN
    default_reason = FailureReason.INTEGRITY_HALT
