"""Engines and connection details. Parameters are hidden so no value reaches a log or an error."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from onepledge_outside_records.domain.errors import OperatorError


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
    """A pooled engine with hidden parameters."""
    return create_engine(sqlalchemy_url(url), hide_parameters=True, pool_pre_ping=True)


class EngineSet:
    """The engines made for one command, disposed together when the command ends."""

    def __init__(self) -> None:
        self._engines: list[Engine] = []

    def engine(self, url: str) -> Engine:
        """A new engine on `url`, remembered for disposal."""
        engine = make_engine(url)
        self._engines.append(engine)
        return engine

    def dispose(self) -> None:
        """Close every pooled connection of every engine."""
        for engine in self._engines:
            engine.dispose()
        self._engines.clear()


@dataclass(frozen=True)
class DatabaseTarget:
    """Names and passwords of one database and its two roles, taken from the two role URLs."""

    database: str
    owner_role: str
    owner_password: str
    app_role: str
    app_password: str

    @classmethod
    def from_urls(cls, migration_url: str, app_url: str) -> DatabaseTarget:
        """Owner from MIGRATION_DATABASE_URL, app role from DATABASE_URL; both must name the same database."""
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
