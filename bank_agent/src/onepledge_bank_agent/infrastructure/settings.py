"""Environment settings of the bank agent (SPEC 3.1, 3.3).

The only module of this package that reads `os.environ`. Settings are scoped to the
command: a runtime process (`worker`, later `serve`) parses `RuntimeSettings`, which
cannot hold an owner or superuser URL; the administrative commands parse
`AdminSettings`. A missing or malformed value stops the command with one sentence
naming the variable, never its value. Database URLs and API keys are secrets and are
masked in every repr.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr

from onepledge_bank_agent.infrastructure.operator_errors import CONFIGURATION_ERROR, OperatorError

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_BANK_CODE = re.compile(r"^[A-Z]{3,12}$")
_BOOLEANS = MappingProxyType({"true": True, "false": False})
MAX_PORT = 65535
MAX_SECONDS = 3600.0
MAX_CHUNK = 1000


def read_environment() -> Mapping[str, str]:
    """The process environment."""
    return os.environ


def _reveal(secret: SecretStr) -> str:
    return secret.get_secret_value()


def _missing(name: str) -> OperatorError:
    return OperatorError(f"Configuration error: {name} is required but not set.", CONFIGURATION_ERROR)


def _invalid(name: str, rule: str) -> OperatorError:
    return OperatorError(f"Configuration error: {name} must be {rule}.", CONFIGURATION_ERROR)


class Tunables(BaseModel):
    """The pipeline tunables of SPEC 3.3 used by intake, cleaning and checks."""

    model_config = ConfigDict(frozen=True)

    pipeline_worker_enabled: bool = True
    pipeline_poll_seconds: float = 1.0
    pipeline_max_attempts: int = 8
    check_chunk_loans: int = 100
    http_connect_timeout_seconds: float = 2.0
    http_read_timeout_seconds: float = 10.0
    title_pending_window_days: int = 30
    first_payment_grace_days: int = 45
    payment_window_days: int = 90
    contract_date_tolerance_days: int = 3


class _Reader:
    """Reads and validates one variable at a time."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    def text(self, name: str) -> str | None:
        value = self._environ.get(name, "").strip()
        return value or None

    def secret(self, name: str) -> SecretStr | None:
        value = self.text(name)
        return SecretStr(value) if value is not None else None

    def matching(self, name: str, pattern: re.Pattern[str], rule: str) -> str | None:
        value = self.text(name)
        if value is not None and not pattern.fullmatch(value):
            raise _invalid(name, rule)
        return value

    def integer(self, name: str, default: int, low: int, high: int) -> int:
        value = self.text(name)
        if value is None:
            return default
        if not value.isdigit() or not low <= int(value) <= high:
            raise _invalid(name, f"a whole number from {low} to {high}")
        return int(value)

    def seconds(self, name: str, default: float) -> float:
        value = self.text(name)
        if value is None:
            return default
        try:
            seconds = float(value)
        except ValueError:
            raise _invalid(name, "a positive number of seconds") from None
        if not 0 < seconds <= MAX_SECONDS:
            raise _invalid(name, "a positive number of seconds")
        return seconds

    def boolean(self, name: str, default: bool) -> bool:
        value = self.text(name)
        if value is None:
            return default
        if value.lower() not in _BOOLEANS:
            raise _invalid(name, "true or false")
        return _BOOLEANS[value.lower()]

    def http_url(self, name: str) -> str | None:
        value = self.text(name)
        if value is not None:
            parts = urlsplit(value)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise _invalid(name, "an http or https URL")
        return value


def _tunables(read: _Reader) -> Tunables:
    return Tunables(
        pipeline_worker_enabled=read.boolean("PIPELINE_WORKER_ENABLED", True),
        pipeline_poll_seconds=read.seconds("PIPELINE_POLL_SECONDS", 1.0),
        pipeline_max_attempts=read.integer("PIPELINE_MAX_ATTEMPTS", 8, 1, 100),
        check_chunk_loans=read.integer("CHECK_CHUNK_LOANS", 100, 1, MAX_CHUNK),
        http_connect_timeout_seconds=read.seconds("HTTP_CONNECT_TIMEOUT_SECONDS", 2.0),
        http_read_timeout_seconds=read.seconds("HTTP_READ_TIMEOUT_SECONDS", 10.0),
        title_pending_window_days=read.integer("TITLE_PENDING_WINDOW_DAYS", 30, 0, 3650),
        first_payment_grace_days=read.integer("FIRST_PAYMENT_GRACE_DAYS", 45, 0, 3650),
        payment_window_days=read.integer("PAYMENT_WINDOW_DAYS", 90, 1, 3650),
        contract_date_tolerance_days=read.integer("CONTRACT_DATE_TOLERANCE_DAYS", 3, 0, 365),
    )


def _log_level(read: _Reader) -> str:
    level = (read.text("LOG_LEVEL") or "INFO").upper()
    if level not in _LOG_LEVELS:
        raise _invalid("LOG_LEVEL", "DEBUG, INFO, WARNING, ERROR or CRITICAL")
    return level


def _bank_code(read: _Reader) -> str | None:
    return read.matching("BANK_CODE", _BANK_CODE, "3 to 12 upper case letters")


def _required[T](name: str, value: T | None, reveal: Callable[[T], str]) -> str:
    if value is None:
        raise _missing(name)
    return reveal(value)


class RuntimeSettings(BaseModel):
    """Settings of a serving or working process (`worker`, and `serve` from WP4).

    It has no field for MIGRATION_DATABASE_URL or PG_ADMIN_URL: a process that handles
    tapes never holds an owner or superuser credential (SPEC_DECISIONS 22, finding 5).
    """

    model_config = ConfigDict(frozen=True)

    bank_code: str | None
    bank_name: str | None
    bind_host: str
    port: int | None
    database_url: SecretStr | None
    outside_records_url: str | None
    outside_records_api_key: SecretStr | None
    log_level: str
    tunables: Tunables

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """Parse the variables of a runtime process that are present; requirements are checked per use."""
        read = _Reader(environ)
        port_text = read.text("PORT")
        return cls(
            bank_code=_bank_code(read),
            bank_name=read.text("BANK_NAME"),
            bind_host=read.text("BIND_HOST") or "127.0.0.1",
            port=read.integer("PORT", 0, 1, MAX_PORT) if port_text is not None else None,
            database_url=read.secret("DATABASE_URL"),
            outside_records_url=read.http_url("OUTSIDE_RECORDS_URL"),
            outside_records_api_key=read.secret("OUTSIDE_RECORDS_API_KEY"),
            log_level=_log_level(read),
            tunables=_tunables(read),
        )

    def require_bank_code(self) -> str:
        """BANK_CODE."""
        return _required("BANK_CODE", self.bank_code, str)

    def require_port(self) -> int:
        """PORT (no default; the launcher always sets it)."""
        if self.port is None:
            raise _missing("PORT")
        return self.port

    def require_database_url(self) -> str:
        """DATABASE_URL (app role)."""
        return _required("DATABASE_URL", self.database_url, _reveal)

    def require_outside_records(self) -> tuple[str, str]:
        """OUTSIDE_RECORDS_URL and OUTSIDE_RECORDS_API_KEY."""
        url = _required("OUTSIDE_RECORDS_URL", self.outside_records_url, str)
        key = _required("OUTSIDE_RECORDS_API_KEY", self.outside_records_api_key, _reveal)
        return url, key


class AdminSettings(BaseModel):
    """Settings of the administrative commands: db provision, migrate and status, and seed-reference."""

    model_config = ConfigDict(frozen=True)

    bank_code: str | None
    database_url: SecretStr | None
    migration_database_url: SecretStr | None
    admin_url: SecretStr | None
    db_app_role: str | None
    log_level: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """Parse the administrative variables that are present; requirements are checked per command."""
        read = _Reader(environ)
        return cls(
            bank_code=_bank_code(read),
            database_url=read.secret("DATABASE_URL"),
            migration_database_url=read.secret("MIGRATION_DATABASE_URL"),
            admin_url=read.secret("PG_ADMIN_URL"),
            db_app_role=read.matching("DB_APP_ROLE", _ROLE_NAME, "a lower case PostgreSQL role name"),
            log_level=_log_level(read),
        )

    def require_bank_code(self) -> str:
        """BANK_CODE."""
        return _required("BANK_CODE", self.bank_code, str)

    def require_database_url(self) -> str:
        """DATABASE_URL (app role)."""
        return _required("DATABASE_URL", self.database_url, _reveal)

    def require_migration_url(self) -> str:
        """MIGRATION_DATABASE_URL (owner role)."""
        return _required("MIGRATION_DATABASE_URL", self.migration_database_url, _reveal)

    def require_admin_url(self) -> str:
        """PG_ADMIN_URL (superuser): `db provision` only."""
        return _required("PG_ADMIN_URL", self.admin_url, _reveal)

    def require_app_role(self) -> str:
        """DB_APP_ROLE."""
        return _required("DB_APP_ROLE", self.db_app_role, str)


def load_runtime_settings() -> RuntimeSettings:
    """Runtime settings from the process environment."""
    return RuntimeSettings.from_env(read_environment())


def load_admin_settings() -> AdminSettings:
    """Administrative settings from the process environment."""
    return AdminSettings.from_env(read_environment())
