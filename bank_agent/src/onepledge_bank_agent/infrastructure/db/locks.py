"""Session level advisory locks, one per submission, on a dedicated connection (SPEC 13.1 step 3).

The lock is released after the stage, and by PostgreSQL itself if the process dies,
because a session lock belongs to its connection.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

from sqlalchemy import Engine, text

TRY_LOCK = text("SELECT pg_try_advisory_lock(hashtextextended('onepledge:submission:' || :submission, 0))")
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
