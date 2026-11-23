"""Environment settings of outside records, scoped to the command (SPEC 3.1, 3.3, SPEC_DECISIONS 22.5).

The only module of this package that reads `os.environ`. `serve` builds `ServeSettings`,
which has no field for MIGRATION_DATABASE_URL or PG_ADMIN_URL, so an owner or superuser
credential present in the environment never reaches the serving process's objects.
The administrative commands build `AdminSettings` and read only the variables their
command needs. Database URLs carry passwords: they are held as secrets and never shown.
"""

from __future__ import annotations

import ipaddress
import os
import re
import unicodedata
from collections.abc import Mapping
from types import MappingProxyType
from typing import Self

from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from onepledge_outside_records.domain.errors import OperatorError
from onepledge_outside_records.domain.text_values import IntegerIssue, parse_integer_text

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MAX_PORT = 65535
_DATABASE_SCHEMES = frozenset({"postgresql", "postgresql+psycopg"})
_ROLE_URLS = frozenset({"DATABASE_URL", "MIGRATION_DATABASE_URL"})
DEFAULT_BIND_HOST = "127.0.0.1"
BIND_HOST_SENTENCE = "BIND_HOST must be an IPv4 or IPv6 address; set it to 127.0.0.1 or 0.0.0.0."
# Settings are trimmed of ASCII whitespace only; any other space is part of the value and fails it.
ASCII_WHITESPACE = " \t\r\n"

# The variables each administrative command reads; nothing else is parsed.
ADMIN_VARIABLES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "provision": frozenset({"PG_ADMIN_URL", "MIGRATION_DATABASE_URL", "DATABASE_URL"}),
        "migrate": frozenset({"MIGRATION_DATABASE_URL", "DB_APP_ROLE"}),
        "seed": frozenset({"MIGRATION_DATABASE_URL"}),
        "clients": frozenset({"MIGRATION_DATABASE_URL"}),
    }
)


def read_environment() -> Mapping[str, str]:
    """The process environment."""
    return os.environ


def _missing(name: str) -> OperatorError:
    return OperatorError(f"Configuration error: {name} is required but not set.", "CONFIGURATION_ERROR")


def _invalid(sentence: str) -> OperatorError:
    return OperatorError(f"Configuration error: {sentence}", "CONFIGURATION_ERROR")


def _unplain(char: str) -> bool:
    """Whitespace other than a space, or a control or format character (Unicode categories Cc, Cf)."""
    return (char.isspace() and char != " ") or unicodedata.category(char) in ("Cc", "Cf")


def _value(environ: Mapping[str, str], name: str) -> str | None:
    """The value trimmed of ASCII whitespace; any other whitespace or control character is refused.

    SPEC_DECISIONS 66: this holds for every setting, secrets included.
    """
    text = environ.get(name, "").strip(ASCII_WHITESPACE)
    if any(_unplain(char) for char in text):
        raise _invalid(f"{name} contains whitespace other than a space, or a control character.")
    return text or None


def _secret(environ: Mapping[str, str], name: str) -> SecretStr | None:
    text = _value(environ, name)
    return SecretStr(text) if text is not None else None


def _database_url(environ: Mapping[str, str], name: str, needs_database: bool) -> SecretStr | None:
    """A `postgresql://` URL checked for its form here, so a malformed one never reaches an engine.

    The value is a secret: the sentence names the variable and the expected form only.
    """
    secret = _secret(environ, name)
    if secret is None:
        return None
    try:
        url = make_url(secret.get_secret_value())
    except (ArgumentError, ValueError):
        url = None
    port_ok = url is not None and (url.port is None or 1 <= url.port <= MAX_PORT)
    if (
        url is None
        or url.drivername not in _DATABASE_SCHEMES
        or not port_ok
        or (needs_database and not url.database)
    ):
        raise _invalid(f"{name} must be a postgresql://USER:PASSWORD@HOST:PORT/DATABASE URL.")
    if name in _ROLE_URLS and (url.query or "?" in secret.get_secret_value()):
        # The service sets its own session parameters; a URL may not change them (SPEC_DECISIONS 103).
        raise _invalid(f"{name} must not carry query parameters; remove the part from ? on.")
    return secret


def _bind_host(environ: Mapping[str, str]) -> str:
    text = _value(environ, "BIND_HOST") or DEFAULT_BIND_HOST
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        raise _invalid(BIND_HOST_SENTENCE) from None


def _port(text: str) -> int:
    """PORT with the sentences every service uses for it (SPEC_DECISIONS 78)."""
    parsed = parse_integer_text(text, 1, MAX_PORT)
    if isinstance(parsed, int):
        return parsed
    digits_only = bool(text) and all("0" <= char <= "9" for char in text.removeprefix("-"))
    if parsed is IntegerIssue.OUT_OF_RANGE or digits_only:
        raise _invalid(f"PORT is out of range (1 to {MAX_PORT:,}).")
    raise _invalid("PORT must be a whole number written with the digits 0 to 9.")


def _log_level(environ: Mapping[str, str]) -> str:
    level = (_value(environ, "LOG_LEVEL") or "INFO").upper()
    if level not in _LOG_LEVELS:
        raise _invalid("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL.")
    return level


class ServeSettings(BaseModel):
    """What `serve` reads: BIND_HOST, PORT, DATABASE_URL (the read-only app role) and LOG_LEVEL."""

    model_config = ConfigDict(frozen=True)

    bind_host: str
    port: int | None
    database_url: SecretStr | None
    log_level: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """Parse the four serving variables; requirements are checked when serving starts."""
        port_text = _value(environ, "PORT")
        port = _port(port_text) if port_text is not None else None
        return cls(
            bind_host=_bind_host(environ),
            port=port,
            database_url=_database_url(environ, "DATABASE_URL", needs_database=True),
            log_level=_log_level(environ),
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


class AdminSettings(BaseModel):
    """What one administrative command reads; variables it does not need stay unset."""

    model_config = ConfigDict(frozen=True)

    database_url: SecretStr | None
    migration_database_url: SecretStr | None
    admin_url: SecretStr | None
    db_app_role: str | None
    log_level: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str], command: str) -> Self:
        """Parse the variables of `command` (a key of ADMIN_VARIABLES) and LOG_LEVEL."""
        needed = ADMIN_VARIABLES[command]
        scoped = {name: environ[name] for name in needed if name in environ}
        role = _value(scoped, "DB_APP_ROLE")
        if role is not None and not _ROLE_NAME.fullmatch(role):
            raise _invalid("DB_APP_ROLE is not a valid role name.")
        return cls(
            database_url=_database_url(scoped, "DATABASE_URL", needs_database=True),
            migration_database_url=_database_url(scoped, "MIGRATION_DATABASE_URL", needs_database=True),
            admin_url=_database_url(scoped, "PG_ADMIN_URL", needs_database=False),
            db_app_role=role,
            log_level=_log_level(environ),
        )

    def require_database_url(self) -> str:
        """DATABASE_URL (app role), read by `db provision` for the role name and password."""
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


def load_serve_settings() -> ServeSettings:
    """Serving settings from the process environment."""
    return ServeSettings.from_env(read_environment())


def load_admin_settings(command: str) -> AdminSettings:
    """Settings of one administrative command from the process environment."""
    return AdminSettings.from_env(read_environment(), command)


def read_secret_variable(name: str) -> str:
    """The value of a variable that holds a secret (for `clients add --key-env`)."""
    value = _value(read_environment(), name)
    if not value:
        raise OperatorError(
            f"The environment variable {name} is not set; export the client key in it first.",
            "CONFIGURATION_ERROR",
        )
    return value
