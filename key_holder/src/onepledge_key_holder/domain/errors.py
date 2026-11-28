"""Business errors of the key holder. Each maps to one error code of `contracts/key_holder.yaml`.

No error carries a request value: an invalid element is named by its index only.
"""

from __future__ import annotations


class KeyHolderError(Exception):
    """Base class of every error the key holder reports to a caller."""


class UnauthenticatedError(KeyHolderError):
    """The API key is missing, repeated, unknown or inactive (401 UNAUTHENTICATED)."""


class InvalidElementError(KeyHolderError):
    """At least one element is not a valid group element (422 INVALID_ELEMENT).

    `indexes` are the positions of the failing elements in the request, in order.
    """

    def __init__(self, indexes: tuple[int, ...]) -> None:
        super().__init__("INVALID_ELEMENT")
        self.indexes = indexes


class KeyVersionUnavailableError(KeyHolderError):
    """The pinned key version is not active or retired-readable here (409 KEY_VERSION_UNAVAILABLE)."""


class RateLimitedError(KeyHolderError):
    """The member's daily element cap is used up (429 RATE_LIMITED)."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("RATE_LIMITED")
        self.retry_after_seconds = retry_after_seconds


class ServiceUnavailableError(KeyHolderError):
    """The holder's database did not answer, so nothing was recorded or returned (503)."""
