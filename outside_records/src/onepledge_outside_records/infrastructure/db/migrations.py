"""Running the Alembic migrations as the owner role (SPEC 6.3 `db migrate`)."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from importlib import resources

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext, MigrationInfo
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.db.engines import make_engine, sqlalchemy_url
from onepledge_outside_records.infrastructure.db.failures import connection_failed
from onepledge_outside_records.infrastructure.db.role_check import owner_problem
from onepledge_outside_records.infrastructure.logging_setup import LogEvent, get_logger

_log = get_logger(__name__)

MIGRATIONS_PACKAGE = "onepledge_outside_records_migrations"
_REPR_MARKS = ("'", '"', "[", "(")
_APP_ROLE_REFUSAL = OperatorError(
    "Configuration error: DB_APP_ROLE must be {database}_app, the app role of the database of "
    "MIGRATION_DATABASE_URL.",
    "CONFIGURATION_ERROR",
)
_DATABASE_OWNER = text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()")


def _log_applied_step(*, step: MigrationInfo, **_: object) -> None:
    """One record per revision Alembic applies or reverts (SPEC_DECISIONS 43.6).

    Alembic's own INFO lines are dropped as library records, so this is the service's own
    event for the fact they carried.
    """
    event = LogEvent.SCHEMA_MIGRATION_APPLIED if step.is_upgrade else LogEvent.SCHEMA_MIGRATION_REVERTED
    for revision in sorted(step.up_revision_ids):
        _log.info(event, revision=revision)


def alembic_config(migration_url: str, app_role: str) -> Config:
    """An Alembic configuration pointing at the packaged migrations and the owner URL."""
    config = Config()
    config.set_main_option("script_location", str(resources.files(MIGRATIONS_PACKAGE)))
    config.attributes["url"] = sqlalchemy_url(migration_url)
    config.attributes["app_role"] = app_role
    config.attributes["on_version_apply"] = _log_applied_step
    return config


class MigrationRunner:
    """Upgrades or downgrades the outside records schema."""

    def __init__(self, migration_url: str, app_role: str) -> None:
        self._migration_url = migration_url
        self._app_role = app_role
        self._config = alembic_config(migration_url, app_role)
        # The revision the database was at when this runner last connected, None at base; and
        # whether it connected at all, since an unreachable database has no known revision.
        self._current: str | None = None
        self._reached = False

    def upgrade(self, revision: str = "head") -> None:
        """Migrate up to `revision`, as the owner of the schema only."""
        self._guarded(
            lambda: self._as_owner(lambda: command.upgrade(self._config, revision)),
            lambda: self._first_pending(revision) or revision,
        )

    def head_revision(self) -> str:
        """The newest revision of the packaged migrations."""
        head = ScriptDirectory.from_config(self._config).get_current_head()
        if head is None:
            raise OperatorError("The packaged migrations have no head revision.", "OPERATION_FAILED")
        return head

    def downgrade(self, revision: str = "base") -> None:
        """Migrate down to `revision`, as the owner of the schema only."""
        self._guarded(
            lambda: self._as_owner(lambda: command.downgrade(self._config, revision)),
            lambda: self._current or revision,
        )

    def _first_pending(self, target: str) -> str | None:
        """The first revision an upgrade from the database's revision towards `target` applies."""
        if not self._reached:
            return None
        try:
            pending = list(ScriptDirectory.from_config(self._config).iterate_revisions(target, self._current))
        except (CommandError, RevisionError):
            return None
        return pending[-1].revision if pending else None

    def _as_owner(self, action: Callable[[], None]) -> None:
        """Run `action` only when MIGRATION_DATABASE_URL connects as the owner and DB_APP_ROLE names
        the app role of that database, which does not own it (SPEC_DECISIONS 72, 103)."""
        if self._app_role != f"{sqlalchemy_url(self._migration_url).database}_app":
            raise _APP_ROLE_REFUSAL
        engine = make_engine(self._migration_url)
        try:
            with engine.connect() as connection:
                problem = owner_problem(connection)
                if problem is not None:
                    raise problem
                if connection.execute(_DATABASE_OWNER).scalar_one() == self._app_role:
                    raise _APP_ROLE_REFUSAL
                self._current = MigrationContext.configure(connection).get_current_revision()
                self._reached = True
        finally:
            engine.dispose()
        action()

    @staticmethod
    def _guarded(action: Callable[[], None], applying: Callable[[], str]) -> None:
        """Every exception while migrating is a migration failure, not the last resort (decision 79).

        The revision named is the one whose script was running, or else the one that was next.
        """
        try:
            action()
        except OperatorError:
            raise
        except Exception as error:  # Every failure of a migration ends here (SPEC_DECISIONS 79).
            # One record with the type, the frames and the revision being applied (SPEC_DECISIONS 66).
            revision = _revision_being_applied(error) or applying()
            _log.error(LogEvent.SCHEMA_MIGRATION_FAILED, exc_info=error, revision=revision)
            raise _operator_error(error, revision) from None


def _revision_being_applied(error: BaseException) -> str | None:
    """The `revision` of the innermost migration script on the stack of the failure, if any."""
    found = None
    for frame, _ in traceback.walk_tb(error.__traceback__):
        names = frame.f_globals
        if "down_revision" in names and isinstance(names.get("revision"), str):
            found = names["revision"]
    return found


def _operator_error(error: BaseException, revision: str) -> OperatorError:
    """The sentence for a failed migration, keeping the exception for `--debug`.

    A connection that never opened names its cause. The sentences the migrations raise
    themselves (about DB_APP_ROLE) and an unknown revision keep theirs; every other failure,
    a statement PostgreSQL refused, a missing privilege or any other error, is one fixed sentence.
    """
    if isinstance(error, OperationalError) and _revision_being_applied(error) is None:
        return connection_failed(error, "MIGRATION_DATABASE_URL")
    if isinstance(error, CommandError):
        sentence = str(error)
        if any(mark in sentence for mark in _REPR_MARKS):
            sentence = (
                "Alembic could not run the migrations: the revision recorded in the database or the "
                "one asked for is not one of the packaged migrations."
            )
        return OperatorError(sentence, "CONFIGURATION_ERROR", error)
    return OperatorError(
        f"The migration to {revision} failed and was rolled back. "
        "Fix the database state named in the log and run db migrate again.",
        "OPERATION_FAILED",
        error,
    )
