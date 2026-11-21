"""Running the Alembic migrations as the owner role (SPEC 6.3 `db migrate`)."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources

from alembic import command
from alembic.config import Config
from alembic.util import CommandError
from sqlalchemy.exc import OperationalError

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.infrastructure.db.engines import sqlalchemy_url

MIGRATIONS_PACKAGE = "onepledge_outside_records_migrations"


def alembic_config(migration_url: str, app_role: str) -> Config:
    """An Alembic configuration pointing at the packaged migrations and the owner URL."""
    config = Config()
    config.set_main_option("script_location", str(resources.files(MIGRATIONS_PACKAGE)))
    config.attributes["url"] = sqlalchemy_url(migration_url)
    config.attributes["app_role"] = app_role
    return config


class MigrationRunner:
    """Upgrades or downgrades the outside records schema."""

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
