"""Alembic environment of a bank database.

The URL and the app role come from `config.attributes` when the migration runner calls
Alembic (`onepledge-bank db migrate`), otherwise from MIGRATION_DATABASE_URL and
DB_APP_ROLE through the package settings (`alembic -c bank_agent/alembic.ini ...`).
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from onepledge_bank_agent.infrastructure.db.engines import sqlalchemy_url
from onepledge_bank_agent.infrastructure.db.models import Base
from onepledge_bank_agent.infrastructure.settings import load_admin_settings


def run_migrations() -> None:
    """Run the migrations online against the owner role, one transaction per revision."""
    config = context.config
    url = config.attributes.get("url")
    if url is None:
        settings = load_admin_settings()
        url = sqlalchemy_url(settings.require_migration_url())
        config.attributes["app_role"] = settings.require_app_role()
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=Base.metadata, transaction_per_migration=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
