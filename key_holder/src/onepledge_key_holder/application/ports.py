"""What the use cases need from outside: time, ids, the scalar, credentials and the journal."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from onepledge_key_holder.domain.evaluation import EvaluationRecord
from onepledge_key_holder.domain.identity import KeyVersion


class Clock(Protocol):
    """The current time, timezone aware."""

    def now(self) -> datetime:
        """The current time."""
        ...


class IdSource(Protocol):
    """New random identifiers."""

    def new_id(self) -> uuid.UUID:
        """A new UUID."""
        ...


class Evaluator(Protocol):
    """Multiplies a point by the secret scalar of one key version."""

    def evaluate(self, point: bytes) -> bytes | None:
        """The 32 bytes of `scalar * point`, or None when `point` is not a ristretto255 point."""
        ...


class Keyring(Protocol):
    """The key versions this holder can evaluate with."""

    def evaluator(self, version: KeyVersion) -> Evaluator | None:
        """The evaluator of an active or retired-readable version, or None."""
        ...


class CredentialDirectory(Protocol):
    """Lookup of a member by the SHA-256 of its API key."""

    def member_for(self, key_sha256: str) -> str | None:
        """The member id of an active credential, or None."""
        ...


class EvaluationJournal(Protocol):
    """The evaluation log and the daily counters, written together."""

    def accept(self, record: EvaluationRecord, window_start: datetime, cap: int) -> bool:
        """Count `record.item_count` in the member's window and append the log row, in one transaction.

        False, with nothing written, when the count would pass `cap`.
        """
        ...
