"""Running the Alembic migrations as the owner role (`db migrate`)."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy.exc import SQLAlchemyError

from onepledge_key_holder.infrastructure.db.engines import OperatorError, sqlalchemy_url

MIGRATIONS_PACKAGE = "onepledge_key_holder_migrations"


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(resources.files(MIGRATIONS_PACKAGE)))
    return config


def installed_head() -> str:
    """The head revision of the migrations installed next to this program."""
    head = ScriptDirectory.from_config(_config()).get_current_head()
    if head is None:
        raise OperatorError("The packaged migrations have no head revision.")
    return head


class MigrationRunner:
    """Upgrades or downgrades the key holder schema with MIGRATION_DATABASE_URL."""

    def __init__(self, migration_url: str) -> None:
        self._config = _config()
        self._config.attributes["url"] = sqlalchemy_url(migration_url)

    def upgrade(self, revision: str = "head") -> str:
        """Migrate up to `revision` and return the head revision of the packaged migrations."""
        self._run(lambda: command.upgrade(self._config, revision))
        return installed_head()

    def downgrade(self, revision: str = "base") -> None:
        """Migrate down to `revision`."""
        self._run(lambda: command.downgrade(self._config, revision))

    @staticmethod
    def _run(action: Callable[[], None]) -> None:
        try:
            action()
        except CommandError as error:
            raise OperatorError(str(error)) from None
        except SQLAlchemyError:
            raise OperatorError(
                "The migration failed and was rolled back: PostgreSQL is not reachable at "
                "MIGRATION_DATABASE_URL, refused its role, or refused a statement. Run db provision "
                "first, or fix the URL."
            ) from None
