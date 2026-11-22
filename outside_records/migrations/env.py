"""Alembic environment of outside records.

The URL comes from `config.attributes["url"]` when the migration runner calls
Alembic, otherwise from MIGRATION_DATABASE_URL through the package settings.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from onepledge_outside_records.infrastructure.db.engines import sqlalchemy_url
from onepledge_outside_records.infrastructure.db.models import Base
from onepledge_outside_records.infrastructure.settings import load_admin_settings


def run_migrations() -> None:
    """Run the migrations online against the owner role."""
    config = context.config
    url = config.attributes.get("url")
    if url is None:
        settings = load_admin_settings("migrate")
        url = sqlalchemy_url(settings.require_migration_url())
        config.attributes["app_role"] = settings.require_app_role()
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                transaction_per_migration=True,
                on_version_apply=config.attributes.get("on_version_apply"),
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
