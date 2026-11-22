"""Real PostgreSQL databases for tests, created through the bank agent's own provisioning and migrations."""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
from sqlalchemy.engine import make_url

from onepledge_bank_agent.domain.enums import Role
from onepledge_bank_agent.infrastructure.db.engines import DatabaseTarget
from onepledge_bank_agent.infrastructure.db.migrations import MigrationRunner
from onepledge_bank_agent.infrastructure.db.provisioning import DatabaseProvisioner
from onepledge_bank_agent.infrastructure.logging import StructlogOperationalLog
from onepledge_bank_agent.infrastructure.settings import RuntimeSettings
from onepledge_testtools.scratch_databases import new_name

ADMIN_URL = os.environ.get("PG_ADMIN_URL", "postgresql://postgres@localhost:55432/postgres")


@dataclass(frozen=True)
class BankDatabase:
    """One test bank database: URLs, role names and its provisioning target."""

    admin_url: str
    owner_url: str
    app_url: str
    app_role: str
    target: DatabaseTarget

    @property
    def name(self) -> str:
        """The database name."""
        return self.target.database

    def environment(self, **extra: str) -> dict[str, str]:
        """The variables the bank agent reads for this database."""
        return {
            "PG_ADMIN_URL": self.admin_url,
            "MIGRATION_DATABASE_URL": self.owner_url,
            "DATABASE_URL": self.app_url,
            "DB_APP_ROLE": self.app_role,
            "BANK_CODE": "HARBOR",
            "BANK_NAME": "Harbor Bank",
            **extra,
        }

    def settings(self, extra: Mapping[str, str] | None = None) -> RuntimeSettings:
        """Runtime settings for this database (plus extra variables)."""
        return RuntimeSettings.from_env(self.environment(**dict(extra or {})))

    def connect(self, role: str) -> psycopg.Connection[tuple[object, ...]]:
        """A psycopg connection as `admin`, `owner` or `app`, in autocommit mode."""
        url = {"admin": self.admin_url, "owner": self.owner_url, "app": self.app_url}[role]
        if role == "admin":
            return psycopg.connect(url, autocommit=True, dbname=self.name)
        return psycopg.connect(url, autocommit=True)


def pairs(database: BankDatabase, query: str, parameters: tuple[object, ...] = ()) -> dict[str, str]:
    """A two column query as the superuser, first column to second, both as text."""
    with database.connect("admin") as connection:
        return {str(row[0]): str(row[1]) for row in connection.execute(query, parameters)}


def migration_runner(database: BankDatabase, app_role: str | None = None) -> MigrationRunner:
    """The product's migration runner on this database's owner URL, logging as the product does."""
    return MigrationRunner(
        database.owner_url, app_role or database.app_role, StructlogOperationalLog("onepledge_bank_agent")
    )


def new_bank_database(code: str = "harbor") -> BankDatabase:
    """Names and passwords for a fresh bank database; nothing is created yet. The name is a scratch
    name of SPEC_DECISIONS 76 (`optest_<UTC time>_<pid>_<8 hex>_bank_<code>`), so the session sweep
    drops it when a killed run left it behind."""
    name = new_name(f"bank_{code}")
    admin = make_url(ADMIN_URL)
    server = f"{admin.host or 'localhost'}:{admin.port or 5432}"
    owner_url = f"postgresql://{name}_owner:{secrets.token_urlsafe(18)}@{server}/{name}"
    app_url = f"postgresql://{name}_app:{secrets.token_urlsafe(18)}@{server}/{name}"
    return BankDatabase(
        ADMIN_URL, owner_url, app_url, f"{name}_app", DatabaseTarget.from_urls(owner_url, app_url)
    )


def provision_and_migrate(database: BankDatabase) -> None:
    """Provision the roles and database and migrate to head, as `make db-up` and `make migrate` will."""
    DatabaseProvisioner(database.admin_url).provision(database.target)
    migration_runner(database).upgrade()


def drop(database: BankDatabase) -> None:
    """Drop the database and both roles."""
    DatabaseProvisioner(database.admin_url).drop(database.target)


def insert_user(database: BankDatabase, role: Role, email: str | None = None) -> uuid.UUID:
    """A bank user row (users and login are built in a later work package; the table exists now)."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    with database.connect("app") as connection:
        connection.execute(
            "INSERT INTO users (user_id, email, display_name, role, password_hash, active, created_at, "
            "updated_at) VALUES (%s, %s, %s, %s, %s, true, %s, %s)",
            (
                user_id,
                email or f"{role.value.lower()}-{user_id.hex[:8]}@harbor.example",
                f"Test {role.value.title()}",
                role.value,
                "$argon2id$v=19$m=65536,t=3,p=4$not-a-real-hash",
                now,
                now,
            ),
        )
    return user_id
