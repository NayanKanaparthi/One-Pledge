"""Session level advisory locks on a dedicated connection: one per submission (SPEC 13.1 step 3) and
`onepledge:outbox`, so one registry outbox sync runs per bank (SPEC 14.1).

The lock is released after the stage, and by PostgreSQL itself if the process dies,
because a session lock belongs to its connection. The periodic outbox sync skips when
another process holds `onepledge:outbox`; a sync whose result decides an exit code or a
submission (`registry sync`, `worker --once`, the halt rule's catch-up) waits for it, up to
`OUTBOX_LOCK_WAIT_SECONDS` and never past a stop request.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Iterator

from sqlalchemy import Connection, Engine, text

from onepledge_bank_agent.application.ports import StopSignal

OUTBOX_KEY = "onepledge:outbox"
TRY_NAMED_LOCK = text("SELECT pg_try_advisory_lock(hashtextextended(:name, 0))")
NAMED_UNLOCK = text("SELECT pg_advisory_unlock(hashtextextended(:name, 0))")
TRY_LOCK = text("SELECT pg_try_advisory_lock(hashtextextended('onepledge:submission:' || :submission, 0))")
OUTBOX_LOCK_WAIT_SECONDS = 120.0
LOCK_RETRY_SECONDS = 0.25
UNLOCK = text("SELECT pg_advisory_unlock(hashtextextended('onepledge:submission:' || :submission, 0))")


class SqlSubmissionLocks:
    """`pg_try_advisory_lock` per submission; a lock another worker holds is reported as not granted."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextlib.contextmanager
    def hold(self, submission_id: uuid.UUID) -> Iterator[bool]:
        """Try the lock for the duration of the block; yields whether it was granted."""
        key = {"submission": str(submission_id)}
        with self._engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            granted = bool(connection.execute(TRY_LOCK, key).scalar_one())
            try:
                yield granted
            finally:
                if granted:
                    connection.execute(UNLOCK, key)


class SqlOutboxLock:
    """`pg_try_advisory_lock` on `onepledge:outbox`; a sync running elsewhere is reported as not granted,
    or waited for when asked."""

    def __init__(
        self, engine: Engine, stop: StopSignal, wait_seconds: float = OUTBOX_LOCK_WAIT_SECONDS
    ) -> None:
        self._engine = engine
        self._stop = stop
        self._wait_seconds = wait_seconds

    @contextlib.contextmanager
    def hold(self, wait: bool = False) -> Iterator[bool]:
        """Take the lock for the duration of the block; yields whether it was granted."""
        key = {"name": OUTBOX_KEY}
        with self._engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            granted = self._take(connection, wait)
            try:
                yield granted
            finally:
                if granted:
                    connection.execute(NAMED_UNLOCK, key)

    def _take(self, connection: Connection, wait: bool) -> bool:
        key = {"name": OUTBOX_KEY}
        deadline = time.monotonic() + (self._wait_seconds if wait else 0.0)
        while True:
            granted = bool(connection.execute(TRY_NAMED_LOCK, key).scalar_one())
            if granted:
                return True
            if time.monotonic() >= deadline or self._stop.wait(LOCK_RETRY_SECONDS):
                return False
