"""Running the Alembic migrations as the owner role, and reading where a database stands (SPEC 12.5).

Every exception raised while a migration runs is a migration failure (SPEC_DECISIONS 79):
one `schema_migration_failed` record with the exception type, its frames and the revision
whose script was running (or the next one), and the sentence naming that revision. A
connection that never opened names that cause instead; an unknown revision keeps Alembic's
own sentence.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import OperationalLog
from onepledge_bank_agent.infrastructure.db.engines import sqlalchemy_url
from onepledge_bank_agent.infrastructure.db.failures import (
    connection_failed,
    migration_failed,
    query_refused,
    sqlstate,
    tables_missing,
)
from onepledge_bank_agent.infrastructure.operator_errors import (
    CONFIGURATION_ERROR,
    INSTALLATION_ERROR,
    SCHEMA_NOT_CURRENT,
    OperatorError,
)

MIGRATIONS_PACKAGE = "onepledge_bank_agent_migrations"
OWNER_VARIABLE = "MIGRATION_DATABASE_URL"


def alembic_config(migration_url: str | None = None, app_role: str | None = None) -> Config:
    """An Alembic configuration pointing at the packaged migrations (and the owner URL when given)."""
    config = Config()
    config.set_main_option("script_location", str(resources.files(MIGRATIONS_PACKAGE)))
    if migration_url is not None:
        config.attributes["url"] = sqlalchemy_url(migration_url)
    if app_role is not None:
        config.attributes["app_role"] = app_role
    return config


def head_revision() -> str:
    """The newest revision of the packaged migrations."""
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise OperatorError(
            "The packaged migrations have no head revision; reinstall the bank agent.", INSTALLATION_ERROR
        )
    return head


class MigrationRunner:
    """Upgrades or downgrades a bank schema as the owner role."""

    def __init__(self, migration_url: str, app_role: str, log: OperationalLog) -> None:
        self._config = alembic_config(migration_url, app_role)
        self._url: str = self._config.attributes["url"]
        self._log = log

    def current_revision(self) -> str | None:
        """The revision the database is at, read as the owner role; None before the first migration."""
        engine = create_engine(self._url, hide_parameters=True)
        try:
            with engine.connect() as connection:
                return MigrationContext.configure(connection).get_current_revision()
        except OperationalError as error:
            raise connection_failed(error, OWNER_VARIABLE) from None
        finally:
            engine.dispose()

    def precondition(self, check: Callable[[], None]) -> None:
        """Run a check that must pass before migrating (the owner mirror check of SPEC_DECISIONS 72) under
        the same failure handling: its failure is recorded as a failed migration to head."""
        self._guarded(check, lambda: "head")

    def upgrade(self, revision: str = "head") -> tuple[str, ...]:
        """Migrate up to `revision`; the revisions this applied, oldest first."""
        before = self._guarded(self.current_revision, lambda: revision)
        self._guarded(lambda: command.upgrade(self._config, revision), lambda: self._next(before, revision))
        after = self.current_revision()
        if after is None or after == before:
            return ()
        script = ScriptDirectory.from_config(self._config)
        applied = script.iterate_revisions(after, before if before is not None else "base")
        return tuple(reversed([step.revision for step in applied]))

    def downgrade(self, revision: str = "base") -> None:
        """Migrate down to `revision`."""
        before = self._guarded(self.current_revision, lambda: revision)
        self._guarded(lambda: command.downgrade(self._config, revision), lambda: before or revision)

    def _next(self, before: str | None, target: str) -> str:
        """The first revision an upgrade from `before` towards `target` applies, or `target`."""
        try:
            script = ScriptDirectory.from_config(self._config)
            pending = list(script.iterate_revisions(target, before if before is not None else "base"))
        except CommandError:
            return target
        return pending[-1].revision if pending else target

    def _guarded[T](self, action: Callable[[], T], applying: Callable[[], str]) -> T:
        """Every exception while migrating is a migration failure, not the last resort (SPEC_DECISIONS 79)."""
        try:
            return action()
        except Exception as error:  # SPEC_DECISIONS 79: every failure of a migration ends here
            revision = _revision_being_applied(error) or applying()
            failure = _failure(error, revision)
            self._log.failure(
                LogEvent.SCHEMA_MIGRATION_FAILED, error, error_code=failure.code, revision=revision
            )
            raise failure from None


def _revision_being_applied(error: BaseException) -> str | None:
    """The `revision` of the innermost migration script on the stack of the failure, if any."""
    found = None
    for frame, _ in traceback.walk_tb(error.__traceback__):
        names = frame.f_globals
        if "down_revision" in names and isinstance(names.get("revision"), str):
            found = names["revision"]
    return found


def _failure(error: BaseException, revision: str) -> OperatorError:
    """The operator sentence of a failed migration: the connection's cause, Alembic's own sentence for an
    unknown revision, a sentence a migration raised itself, or the fixed sentence naming the revision."""
    if isinstance(error, OperatorError):
        return error
    if isinstance(error, OperationalError) and _revision_being_applied(error) is None:
        return connection_failed(error, OWNER_VARIABLE)
    if isinstance(error, CommandError):
        return OperatorError(str(error), CONFIGURATION_ERROR)
    return migration_failed(revision)


@dataclass(frozen=True)
class SchemaStatus:
    """Where a database stands against the packaged migrations."""

    database: str
    current_revision: str | None
    head_revision: str

    @property
    def at_head(self) -> bool:
        """True when the database is migrated to the newest revision."""
        return self.current_revision == self.head_revision


class SchemaInspector:
    """Reads the current revision through the app role (which may only SELECT `alembic_version`)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def status(self) -> SchemaStatus:
        """The current and head revisions, or one sentence saying what to run."""
        try:
            with self._engine.connect() as connection:
                database: str = connection.execute(text("SELECT current_database()")).scalar_one()
                exists: str | None = connection.execute(
                    text("SELECT to_regclass('public.alembic_version')::text")
                ).scalar_one()
                current = (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
                    if exists is not None
                    else None
                )
        except OperationalError as error:
            raise connection_failed(error, "DATABASE_URL") from None
        except DBAPIError as error:
            raise query_refused(sqlstate(error), "DATABASE_URL") from None
        return SchemaStatus(str(database), current, head_revision())

    def require_head(self) -> None:
        """Refuse to run against a database that is not migrated to the newest revision; a database that
        was never migrated says its tables are missing (SPEC_DECISIONS 78: the real cause)."""
        status = self.status()
        if status.current_revision is None:
            raise tables_missing()
        if not status.at_head:
            raise OperatorError(
                f"The database {status.database} is not at the latest revision; run db migrate.",
                SCHEMA_NOT_CURRENT,
            )
