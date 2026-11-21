"""Environment settings of outside records (SPEC 3.1, 3.3).

The only module of this package that reads `os.environ`. Database URLs carry
passwords, so they are held as secrets and never shown in a repr.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, SecretStr

from onepledge_outside_records.domain.errors import OperatorError

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MAX_PORT = 65535


def read_environment() -> Mapping[str, str]:
    """The process environment."""
    return os.environ


def _missing(name: str) -> OperatorError:
    return OperatorError(f"Configuration error: {name} is required but not set.")


class Settings(BaseModel):
    """Immutable settings. Each command asks only for the values it needs."""

    model_config = ConfigDict(frozen=True)

    bind_host: str
    port: int | None
    database_url: SecretStr | None
    migration_database_url: SecretStr | None
    admin_url: SecretStr | None
    db_app_role: str | None
    log_level: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """Parse the variables that are present; requirements are checked per command."""

        def value(name: str) -> str | None:
            text = environ.get(name, "").strip()
            return text or None

        port_text = value("PORT")
        if port_text is not None and (not port_text.isdigit() or not 1 <= int(port_text) <= MAX_PORT):
            raise OperatorError("Configuration error: PORT must be a TCP port number.")
        level = (value("LOG_LEVEL") or "INFO").upper()
        if level not in _LOG_LEVELS:
            raise OperatorError(
                "Configuration error: LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL."
            )
        role = value("DB_APP_ROLE")
        if role is not None and not _ROLE_NAME.fullmatch(role):
            raise OperatorError("Configuration error: DB_APP_ROLE is not a valid role name.")
        secrets = {name: value(name) for name in ("DATABASE_URL", "MIGRATION_DATABASE_URL", "PG_ADMIN_URL")}
        return cls(
            bind_host=value("BIND_HOST") or "127.0.0.1",
            port=int(port_text) if port_text is not None else None,
            database_url=SecretStr(secrets["DATABASE_URL"]) if secrets["DATABASE_URL"] else None,
            migration_database_url=(
                SecretStr(secrets["MIGRATION_DATABASE_URL"]) if secrets["MIGRATION_DATABASE_URL"] else None
            ),
            admin_url=SecretStr(secrets["PG_ADMIN_URL"]) if secrets["PG_ADMIN_URL"] else None,
            db_app_role=role,
            log_level=level,
        )

    def require_port(self) -> int:
        """PORT, or a one sentence error."""
        if self.port is None:
            raise _missing("PORT")
        return self.port

    def require_database_url(self) -> str:
        """DATABASE_URL (app role)."""
        if self.database_url is None:
            raise _missing("DATABASE_URL")
        return self.database_url.get_secret_value()

    def require_migration_url(self) -> str:
        """MIGRATION_DATABASE_URL (owner role)."""
        if self.migration_database_url is None:
            raise _missing("MIGRATION_DATABASE_URL")
        return self.migration_database_url.get_secret_value()

    def require_admin_url(self) -> str:
        """PG_ADMIN_URL (superuser, provisioning only)."""
        if self.admin_url is None:
            raise _missing("PG_ADMIN_URL")
        return self.admin_url.get_secret_value()

    def require_app_role(self) -> str:
        """DB_APP_ROLE."""
        if self.db_app_role is None:
            raise _missing("DB_APP_ROLE")
        return self.db_app_role


def load_settings() -> Settings:
    """Settings from the process environment."""
    return Settings.from_env(read_environment())


def read_secret_variable(name: str) -> str:
    """The value of a variable that holds a secret (for `clients add --key-env`)."""
    value = read_environment().get(name, "")
    if not value:
        raise OperatorError(f"The environment variable {name} is not set; export the client key in it first.")
    return value
