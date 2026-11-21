"""Running the Alembic migrations as the owner role, and reading where a database stands (SPEC 12.5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from onepledge_bank_agent.infrastructure.db.engines import sqlalchemy_url
from onepledge_bank_agent.infrastructure.operator_errors import OperatorError

MIGRATIONS_PACKAGE = "onepledge_bank_agent_migrations"


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
        raise OperatorError("The packaged migrations have no head revision; reinstall the bank agent.")
    return head


class MigrationRunner:
    """Upgrades or downgrades a bank schema as the owner role."""

    def __init__(self, migration_url: str, app_role: str) -> None:
        self._config = alembic_config(migration_url, app_role)

    def upgrade(self, revision: str = "head") -> None:
        """Migrate up to `revision`."""
        self._guarded(lambda: command.upgrade(self._config, revision))

    def downgrade(self, revision: str = "base") -> None:
        """Migrate down to `revision`."""
        self._guarded(lambda: command.downgrade(self._config, revision))

    @staticmethod
    def _guarded(action: Callable[[], None]) -> None:
        try:
            action()
        except OperationalError:
            raise OperatorError("PostgreSQL refused the owner connection; run db provision first.") from None
        except CommandError as error:
            raise OperatorError(str(error)) from None


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
        except OperationalError:
            raise OperatorError(
                "PostgreSQL refused the connection at DATABASE_URL; run db provision first."
            ) from None
        except DBAPIError:
            raise OperatorError(
                "The database at DATABASE_URL cannot be read; run db migrate first."
            ) from None
        return SchemaStatus(str(database), current, head_revision())

    def require_head(self) -> None:
        """Refuse to run against a database that is not migrated to the newest revision."""
        status = self.status()
        if not status.at_head:
            raise OperatorError(
                f"The database {status.database} is not at the latest revision; run db migrate."
            )
