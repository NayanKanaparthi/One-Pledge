"""Named crash points of the pipeline and the `FaultInjector` port (SPEC 21.3).

Production uses `NoFaults`, which does nothing. The reliability tests inject an
implementation that raises at a named point, unwinding everything, and then start a
new worker on the same database.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class FaultPoint(StrEnum):
    """A point where the reliability suite crashes the pipeline."""

    AFTER_INTAKE_COMMIT = "AFTER_INTAKE_COMMIT"
    CLEAN_BEFORE_COMMIT = "CLEAN_BEFORE_COMMIT"
    AFTER_CHECK_CHUNK = "AFTER_CHECK_CHUNK"
    AFTER_KEY_HOLDER_RESPONSE = "AFTER_KEY_HOLDER_RESPONSE"
    AFTER_CODES_STORED = "AFTER_CODES_STORED"
    AFTER_REQUESTS_PLANNED = "AFTER_REQUESTS_PLANNED"
    AFTER_REGISTRY_RESPONSE = "AFTER_REGISTRY_RESPONSE"
    AFTER_RESPONSE_STORED = "AFTER_RESPONSE_STORED"
    AFTER_CERTIFICATE_STORED = "AFTER_CERTIFICATE_STORED"
    BETWEEN_OUTBOX_COMMIT_AND_ACK = "BETWEEN_OUTBOX_COMMIT_AND_ACK"


class FaultInjector(Protocol):
    """Called when the pipeline passes a named point."""

    def reached(self, point: FaultPoint) -> None:
        """Do nothing, or raise to simulate a crash at this point."""
        ...


class NoFaults:
    """The production injector: passing a point has no effect."""

    def reached(self, point: FaultPoint) -> None:
        """Nothing happens."""
