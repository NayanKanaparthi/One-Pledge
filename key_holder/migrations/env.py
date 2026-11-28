"""Alembic environment of the key holder database.

The URL comes from `config.attributes["url"]` when a migration runner calls Alembic,
otherwise from MIGRATION_DATABASE_URL through the package settings. It connects with the
psycopg driver and runs each revision in its own transaction.
"""

from __future__ import annotations

from alembic import context
from onepledge_key_holder.infrastructure.settings import load_migration_url
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def run_migrations() -> None:
    """Run the migrations online against the owner role."""
    config = context.config
    url = config.attributes.get("url")
    if url is None:
        url = make_url(load_migration_url()).set(drivername="postgresql+psycopg")
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
