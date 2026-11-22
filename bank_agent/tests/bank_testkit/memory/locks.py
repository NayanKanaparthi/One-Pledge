"""In-memory advisory locks: the per submission lock of the worker and the bank's outbox lock."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator


class MemorySubmissionLocks:
    """`SubmissionLocks`: a submission in `held` is locked by another worker."""

    def __init__(self) -> None:
        self.held: set[uuid.UUID] = set()
        self.taken: list[uuid.UUID] = []

    @contextlib.contextmanager
    def hold(self, submission_id: uuid.UUID) -> Iterator[bool]:
        """Granted unless another worker holds it; released on exit."""
        if submission_id in self.held:
            yield False
            return
        self.held.add(submission_id)
        self.taken.append(submission_id)
        try:
            yield True
        finally:
            self.held.discard(submission_id)


class MemoryOutboxLock:
    """`OutboxLock`: `busy` means another sync holds the bank's outbox lock."""

    def __init__(self) -> None:
        self.busy = False

    @contextlib.contextmanager
    def hold(self) -> Iterator[bool]:
        """Granted unless busy."""
        if self.busy:
            yield False
            return
        self.busy = True
        try:
            yield True
        finally:
            self.busy = False
