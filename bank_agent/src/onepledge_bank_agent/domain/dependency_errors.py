"""Named errors of the two shared services and of the bank's checks on what they return (SPEC 9.4, 13).

Each is a `PipelineStageError`: `transient` decides whether the stage is retried with
backoff; `failure_code` is what a FAILED submission records; `dependency`,
`http_status` and `reason` go into the `TAPE_FAILED` audit entry (SPEC_DECISIONS 26).
"""

from __future__ import annotations

from onepledge_bank_agent.domain.enums import DependencyRefusal, FailureCode
from onepledge_bank_agent.domain.errors import PipelineStageError

KEY_HOLDER_1 = "key_holder_1"
KEY_HOLDER_2 = "key_holder_2"
REGISTRY = "registry"


class KeyHolderUnavailableError(PipelineStageError):
    """A key holder could not be reached or answered 429 or 5xx after the client's retries."""

    code = "KEY_HOLDER_UNAVAILABLE"
    transient = True
    failure_code = FailureCode.KEY_HOLDER_UNAVAILABLE

    def __init__(self, dependency: str, http_status: int | None = None) -> None:
        super().__init__(http_status)
        self.dependency = dependency


class KeyHolderRejectedError(PipelineStageError):
    """A key holder refused the request (401, 409, 413, 422 or another 4xx)."""

    code = "KEY_HOLDER_REJECTED"
    failure_code = FailureCode.KEY_HOLDER_REJECTED

    def __init__(self, dependency: str, http_status: int, reason: DependencyRefusal) -> None:
        super().__init__(http_status, reason)
        self.dependency = dependency


class KeyHolderProtocolError(PipelineStageError):
    """A key holder answered outside the contract: a wrong count, or a malformed or invalid element."""

    code = "KEY_HOLDER_PROTOCOL"
    failure_code = FailureCode.KEY_HOLDER_REJECTED

    def __init__(self, dependency: str, http_status: int | None) -> None:
        super().__init__(http_status, DependencyRefusal.MALFORMED_RESPONSE)
        self.dependency = dependency


class KeyVersionChangedError(PipelineStageError):
    """A tokenise round reported a key version pair other than the one this submission's tokens use."""

    code = "KEY_VERSION_CHANGED"
    failure_code = FailureCode.KEY_VERSION_MISMATCH

    def __init__(self, dependency: str) -> None:
        super().__init__()
        self.dependency = dependency


class RegistryUnavailableError(PipelineStageError):
    """The registry could not be reached or answered 429, 500, 502, 503 or 504 after the client's retries."""

    code = "REGISTRY_UNAVAILABLE"
    transient = True
    failure_code = FailureCode.REGISTRY_UNAVAILABLE
    dependency = REGISTRY

    def __init__(self, http_status: int | None = None) -> None:
        super().__init__(http_status)


class RegistryRejectedError(PipelineStageError):
    """The registry refused the request: 401, 409 IDEMPOTENCY_KEY_REUSED, 413, 422 or another 4xx."""

    code = "REGISTRY_REJECTED_REQUEST"
    failure_code = FailureCode.REGISTRY_REJECTED_REQUEST
    dependency = REGISTRY

    def __init__(self, http_status: int, reason: DependencyRefusal) -> None:
        super().__init__(http_status, reason)


class RegistryProtocolError(PipelineStageError):
    """The registry answered outside the contract, or with results that do not match what was sent."""

    code = "REGISTRY_PROTOCOL"
    failure_code = FailureCode.REGISTRY_REJECTED_REQUEST
    dependency = REGISTRY

    def __init__(self, http_status: int | None) -> None:
        super().__init__(http_status, DependencyRefusal.MALFORMED_RESPONSE)


class KeyVersionMismatchError(PipelineStageError):
    """The registry refused the key version of the codes (409 KEY_VERSION_MISMATCH). Not retryable."""

    code = "KEY_VERSION_MISMATCH"
    failure_code = FailureCode.KEY_VERSION_MISMATCH
    dependency = REGISTRY

    def __init__(self, http_status: int) -> None:
        super().__init__(http_status, DependencyRefusal.REQUEST_REFUSED)


class CaseAlreadyResolvedError(PipelineStageError):
    """The registry already resolved the case (409 CASE_ALREADY_RESOLVED)."""

    code = "CASE_ALREADY_RESOLVED"
    failure_code = FailureCode.REGISTRY_REJECTED_REQUEST
    dependency = REGISTRY

    def __init__(self, http_status: int) -> None:
        super().__init__(http_status, DependencyRefusal.REQUEST_REFUSED)


class CertificateInvalidError(PipelineStageError):
    """A certificate of the submission failed the bank's own verification (SPEC 13.6). Not retryable."""

    code = "CERTIFICATE_INVALID"
    failure_code = FailureCode.CERTIFICATE_INVALID


class RegistryIntegrityBrokenError(PipelineStageError):
    """A received checkpoint failed verification and the halt rule is on: nothing is sent (SPEC 15.4)."""

    code = "REGISTRY_INTEGRITY_BROKEN"
    failure_code = FailureCode.REGISTRY_INTEGRITY_BROKEN
