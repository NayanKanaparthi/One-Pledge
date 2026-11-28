"""Engines and connection details. Parameters are hidden so no value reaches a log or an error."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

# Every session runs in UTC with bounded waits, whatever the server, role or database default.
SESSION_OPTIONS = "-c TimeZone=UTC -c search_path=public -c statement_timeout=5s -c lock_timeout=2s"


class OperatorError(Exception):
    """A command cannot do its work. `sentence` tells the operator why and never holds a secret."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


def sqlalchemy_url(url: str) -> URL:
    """A libpq style `postgresql://` URL as a SQLAlchemy URL with the psycopg driver."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        raise OperatorError("A database URL is malformed; check the postgresql:// variables.") from None
    if parsed.drivername not in ("postgresql", "postgresql+psycopg"):
        raise OperatorError("A database URL must start with postgresql://.")
    return parsed.set(drivername="postgresql+psycopg")


def make_engine(url: str) -> Engine:
    """A pooled engine with hidden parameters and the service's own session options."""
    return create_engine(
        sqlalchemy_url(url),
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={"options": SESSION_OPTIONS, "client_encoding": "UTF8"},
    )


@dataclass(frozen=True)
class DatabaseTarget:
    """Names and passwords of one holder's database and its two roles, taken from the two role URLs."""

    database: str
    owner_role: str
    owner_password: str
    app_role: str
    app_password: str

    @classmethod
    def from_urls(cls, migration_url: str, app_url: str) -> DatabaseTarget:
        """Owner from MIGRATION_DATABASE_URL, app role from DATABASE_URL; both name the same database."""
        owner, app = sqlalchemy_url(migration_url), sqlalchemy_url(app_url)
        if not owner.database or owner.database != app.database:
            raise OperatorError("DATABASE_URL and MIGRATION_DATABASE_URL must name the same database.")
        owner_role, app_role = f"{owner.database}_owner", f"{owner.database}_app"
        if (owner.username, app.username) != (owner_role, app_role):
            raise OperatorError(
                "The roles must be named {database}_owner (MIGRATION_DATABASE_URL) and {database}_app "
                "(DATABASE_URL)."
            )
        if not owner.password or not app.password:
            raise OperatorError("DATABASE_URL and MIGRATION_DATABASE_URL must both carry a password.")
        return cls(owner.database, owner_role, owner.password, app_role, app.password)

    def __repr__(self) -> str:
        """Never show the passwords."""
        return f"DatabaseTarget(database={self.database!r}, owner={self.owner_role!r}, app={self.app_role!r})"
