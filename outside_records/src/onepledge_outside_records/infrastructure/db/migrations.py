"""Running the Alembic migrations as the owner role (SPEC 6.3 `db migrate`)."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from importlib import resources

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationInfo
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy.exc import OperationalError

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.db.engines import sqlalchemy_url
from onepledge_outside_records.infrastructure.logging_setup import LogEvent, get_logger

_log = get_logger(__name__)

MIGRATIONS_PACKAGE = "onepledge_outside_records_migrations"
_REPR_MARKS = ("'", '"', "[", "(")


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
        self._config = alembic_config(migration_url, app_role)

    def upgrade(self, revision: str = "head") -> None:
        """Migrate up to `revision`."""
        self._guarded(lambda: command.upgrade(self._config, revision), revision)

    def head_revision(self) -> str:
        """The newest revision of the packaged migrations."""
        head = ScriptDirectory.from_config(self._config).get_current_head()
        if head is None:
            raise OperatorError("The packaged migrations have no head revision.", "OPERATION_FAILED")
        return head

    def downgrade(self, revision: str = "base") -> None:
        """Migrate down to `revision`."""
        self._guarded(lambda: command.downgrade(self._config, revision), revision)

    @staticmethod
    def _guarded(action: Callable[[], None], target: str) -> None:
        try:
            action()
        except (OperationalError, CommandError) as error:
            # One record with the type, the frames and the revision being applied (SPEC_DECISIONS 66).
            revision = _revision_being_applied(error) or target
            _log.error(LogEvent.SCHEMA_MIGRATION_FAILED, exc_info=error, revision=revision)
            raise _operator_error(error) from None


def _revision_being_applied(error: BaseException) -> str | None:
    """The `revision` of the innermost migration script on the stack of the failure, if any."""
    found = None
    for frame, _ in traceback.walk_tb(error.__traceback__):
        names = frame.f_globals
        if "down_revision" in names and isinstance(names.get("revision"), str):
            found = names["revision"]
    return found


def _operator_error(error: BaseException) -> OperatorError:
    """The fixed sentence for a failed migration, keeping the exception for `--debug`."""
    if isinstance(error, OperationalError):
        return OperatorError(
            "PostgreSQL refused the owner connection; run db provision first.", "DATABASE_UNAVAILABLE", error
        )
    # The migrations raise plain sentences of their own (DB_APP_ROLE); Alembic's own
    # messages quote revision ids, so those become one fixed sentence (SPEC_DECISIONS 43.11).
    sentence = str(error)
    if any(mark in sentence for mark in _REPR_MARKS):
        sentence = (
            "Alembic could not run the migrations: the revision recorded in the database or the "
            "one asked for is not one of the packaged migrations."
        )
    return OperatorError(sentence, "CONFIGURATION_ERROR", error)
