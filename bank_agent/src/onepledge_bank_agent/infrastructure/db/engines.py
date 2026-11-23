"""Engines and database targets. Parameters are hidden so no value reaches a log or an error."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from onepledge_bank_agent.infrastructure.db.connection_settings import (
    connect_options,
    refuse_replica_sessions,
)
from onepledge_bank_agent.infrastructure.operator_errors import CONFIGURATION_ERROR, OperatorError


def sqlalchemy_url(url: str) -> URL:
    """A libpq style `postgresql://` URL as a SQLAlchemy URL with the psycopg driver."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        raise OperatorError(
            "A database URL is malformed; check the postgresql:// variables.", CONFIGURATION_ERROR
        ) from None
    if parsed.drivername not in ("postgresql", "postgresql+psycopg"):
        raise OperatorError("A database URL must start with postgresql://.", CONFIGURATION_ERROR)
    return parsed.set(drivername="postgresql+psycopg")


def make_engine(url: str, pool_size: int = 5, *, inspecting: bool = False) -> Engine:
    """A pooled engine with hidden parameters. Every connection is opened with the bank agent's own
    session parameters, and a new one whose session would not fire triggers is refused
    (SPEC_DECISIONS 103.7, `connection_settings.py`). An `inspecting` engine, which only the role
    inspection and the database controls use, keeps such a session, so they can name it."""
    engine = create_engine(
        sqlalchemy_url(url),
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=pool_size,
        connect_args={"options": connect_options()},
    )
    if not inspecting:
        refuse_replica_sessions(engine)
    return engine


class EngineSet:
    """The engines of one process, one per URL, disposed together when the process ends."""

    def __init__(self) -> None:
        self._engines: dict[tuple[str, bool], Engine] = {}

    def engine(self, url: str, *, inspecting: bool = False) -> Engine:
        """The engine on `url`, made on first use; an `inspecting` one is separate (see `make_engine`)."""
        key = (url, inspecting)
        if key not in self._engines:
            self._engines[key] = make_engine(url, inspecting=inspecting)
        return self._engines[key]

    def dispose(self) -> None:
        """Close every pooled connection of every engine."""
        for engine in self._engines.values():
            engine.dispose()
        self._engines.clear()


@dataclass(frozen=True, repr=False)
class DatabaseTarget:
    """One bank database and its two roles, read from the owner and app role URLs (SPEC_DECISIONS 17)."""

    database: str
    owner_role: str
    owner_password: str
    app_role: str
    app_password: str

    @classmethod
    def from_urls(cls, migration_url: str, app_url: str) -> DatabaseTarget:
        """Owner from MIGRATION_DATABASE_URL, app role from DATABASE_URL, both on one database."""
        owner, app = sqlalchemy_url(migration_url), sqlalchemy_url(app_url)
        if not owner.database or owner.database != app.database:
            raise OperatorError(
                "DATABASE_URL and MIGRATION_DATABASE_URL must name the same database.", CONFIGURATION_ERROR
            )
        owner_role, app_role = f"{owner.database}_owner", f"{owner.database}_app"
        if (owner.username, app.username) != (owner_role, app_role):
            raise OperatorError(
                "The roles must be named {database}_owner (MIGRATION_DATABASE_URL) and {database}_app "
                "(DATABASE_URL).",
                CONFIGURATION_ERROR,
            )
        if not owner.password or not app.password:
            raise OperatorError(
                "DATABASE_URL and MIGRATION_DATABASE_URL must both carry a password.", CONFIGURATION_ERROR
            )
        return cls(owner.database, owner_role, owner.password, app_role, app.password)

    def __repr__(self) -> str:
        """Never show the passwords."""
        return f"DatabaseTarget(database={self.database!r}, owner={self.owner_role!r}, app={self.app_role!r})"
