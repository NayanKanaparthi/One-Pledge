"""What `serve` reads and writes as the app role: credentials, key versions, the journal.

No statement here has a parameter that could hold an element: the journal writes a member
id, a request id, a count and a time (SPEC 4.1, decision 12). A database that does not
answer is `ServiceUnavailableError`, so nothing is returned that was not recorded.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from onepledge_key_holder.domain.errors import ServiceUnavailableError
from onepledge_key_holder.domain.evaluation import EvaluationRecord
from onepledge_key_holder.domain.identity import KeyVersion
from onepledge_key_holder.infrastructure.db.engines import OperatorError, make_engine
from onepledge_key_holder.infrastructure.db.registration import ACTIVE

_MEMBER = text("SELECT member_id::text FROM api_credentials WHERE key_sha256 = :key_sha256 AND active")
_STATUS = text("SELECT status FROM key_versions WHERE key_version = :key_version")
# One statement decides the cap: the counter moves only when the new total stays inside it.
_COUNT = text(
    "INSERT INTO rate_limits (member_id, window_start, items_used) "
    "VALUES (:member_id, :window_start, :items) "
    "ON CONFLICT (member_id, window_start) DO UPDATE "
    "SET items_used = rate_limits.items_used + EXCLUDED.items_used "
    "WHERE rate_limits.items_used + EXCLUDED.items_used <= :cap "
    "RETURNING items_used"
)
_APPEND = text(
    "INSERT INTO evaluation_log (eval_id, member_id, request_id, item_count, at) "
    "VALUES (:eval_id, :member_id, :request_id, :item_count, :at)"
)


class SqlCredentialDirectory:
    """`api_credentials`, looked up by the SHA-256 of the presented key."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def member_for(self, key_sha256: str) -> str | None:
        """The member id of an active credential, or None."""
        try:
            with self._engine.connect() as connection:
                member_id: str | None = connection.execute(_MEMBER, {"key_sha256": key_sha256}).scalar()
        except SQLAlchemyError:
            raise ServiceUnavailableError from None
        return member_id


class SqlEvaluationJournal:
    """`rate_limits` and `evaluation_log`, written in one transaction per accepted request."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def accept(self, record: EvaluationRecord, window_start: datetime, cap: int) -> bool:
        """Count the elements and append the log row; False, with nothing written, over the cap."""
        if record.item_count > cap:
            return False
        try:
            with self._engine.begin() as connection:
                counted = connection.execute(
                    _COUNT,
                    {
                        "member_id": record.member_id,
                        "window_start": window_start,
                        "items": record.item_count,
                        "cap": cap,
                    },
                ).first()
                if counted is None:
                    return False
                connection.execute(
                    _APPEND,
                    {
                        "eval_id": record.eval_id,
                        "member_id": record.member_id,
                        "request_id": record.request_id,
                        "item_count": record.item_count,
                        "at": record.at,
                    },
                )
        except SQLAlchemyError:
            raise ServiceUnavailableError from None
        return True


class Database:
    """The serving process's database: one engine on DATABASE_URL, the app role."""

    def __init__(self, database_url: str) -> None:
        self._engine = make_engine(database_url)

    def credentials(self) -> SqlCredentialDirectory:
        """The credential lookup."""
        return SqlCredentialDirectory(self._engine)

    def journal(self) -> SqlEvaluationJournal:
        """The evaluation log and the daily counters."""
        return SqlEvaluationJournal(self._engine)

    def require_active(self, version: KeyVersion) -> None:
        """Refuse to start unless `key_versions` records `version` as the active one."""
        try:
            with self._engine.connect() as connection:
                status = connection.execute(_STATUS, {"key_version": version.number}).scalar()
        except SQLAlchemyError:
            raise OperatorError(
                "The database of DATABASE_URL is not reachable or has no key holder tables; "
                "run db provision and db migrate first, or fix the URL."
            ) from None
        if status != ACTIVE:
            raise OperatorError(
                "ACTIVE_KEY_VERSION is not the active key version recorded in the database; "
                "run db migrate with the same ACTIVE_KEY_VERSION first."
            )

    def dispose(self) -> None:
        """Close every pooled connection."""
        self._engine.dispose()
