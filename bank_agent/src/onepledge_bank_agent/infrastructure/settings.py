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
import uuid
from collections.abc import Callable, Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, SecretStr

from onepledge_bank_agent.domain.text_rules import ascii_upper
from onepledge_bank_agent.infrastructure.operator_errors import CONFIGURATION_ERROR, OperatorError
from onepledge_bank_agent.infrastructure.setting_reader import SettingReader, invalid

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_BANK_CODE = re.compile(r"^[A-Z]{3,12}$")
MAX_PORT = 65535
MAX_CHUNK = 1000
MAX_TOKENIZE_CHUNK = 125  # 8 elements per loan, at most 1,000 elements per key holder request
MAX_SUBMIT_BATCH = 500


def read_environment() -> Mapping[str, str]:
    """The process environment."""
    return os.environ


def _reveal(secret: SecretStr) -> str:
    return secret.get_secret_value()


def _missing(name: str) -> OperatorError:
    return OperatorError(f"Configuration error: {name} is required but not set.", CONFIGURATION_ERROR)


class Tunables(BaseModel):
    """The pipeline tunables of SPEC 3.3 used by intake, cleaning and checks."""

    model_config = ConfigDict(frozen=True)

    pipeline_worker_enabled: bool = True
    pipeline_poll_seconds: float = 1.0
    pipeline_max_attempts: int = 8
    outbox_poll_seconds: float = 2.0
    check_chunk_loans: int = 100
    tokenize_chunk_loans: int = 100
    submit_batch_items: int = 500
    http_connect_timeout_seconds: float = 2.0
    http_read_timeout_seconds: float = 10.0
    title_pending_window_days: int = 30
    first_payment_grace_days: int = 45
    payment_window_days: int = 90
    contract_date_tolerance_days: int = 3
    halt_on_registry_integrity_failure: bool = True


def _tunables(read: SettingReader) -> Tunables:
    return Tunables(
        pipeline_worker_enabled=read.boolean("PIPELINE_WORKER_ENABLED", True),
        pipeline_poll_seconds=read.seconds("PIPELINE_POLL_SECONDS", 1.0),
        pipeline_max_attempts=read.integer("PIPELINE_MAX_ATTEMPTS", 8, 1, 100),
        outbox_poll_seconds=read.seconds("OUTBOX_POLL_SECONDS", 2.0),
        check_chunk_loans=read.integer("CHECK_CHUNK_LOANS", 100, 1, MAX_CHUNK),
        tokenize_chunk_loans=read.integer("TOKENIZE_CHUNK_LOANS", 100, 1, MAX_TOKENIZE_CHUNK),
        submit_batch_items=read.integer("SUBMIT_BATCH_ITEMS", 500, 1, MAX_SUBMIT_BATCH),
        http_connect_timeout_seconds=read.seconds("HTTP_CONNECT_TIMEOUT_SECONDS", 2.0),
        http_read_timeout_seconds=read.seconds("HTTP_READ_TIMEOUT_SECONDS", 10.0),
        title_pending_window_days=read.integer("TITLE_PENDING_WINDOW_DAYS", 30, 0, 3650),
        first_payment_grace_days=read.integer("FIRST_PAYMENT_GRACE_DAYS", 45, 0, 3650),
        payment_window_days=read.integer("PAYMENT_WINDOW_DAYS", 90, 1, 3650),
        contract_date_tolerance_days=read.integer("CONTRACT_DATE_TOLERANCE_DAYS", 3, 0, 365),
        halt_on_registry_integrity_failure=read.boolean("HALT_ON_REGISTRY_INTEGRITY_FAILURE", True),
    )


def _log_level(read: SettingReader) -> str:
    level = ascii_upper(read.text("LOG_LEVEL") or "INFO")
    if level not in _LOG_LEVELS:
        raise invalid("LOG_LEVEL", "DEBUG, INFO, WARNING, ERROR or CRITICAL")
    return level


def _bank_code(read: SettingReader) -> str | None:
    return read.matching("BANK_CODE", _BANK_CODE, "3 to 12 upper case letters")


def _required[T](name: str, value: T | None, reveal: Callable[[T], str]) -> str:
    if value is None:
        raise _missing(name)
    return reveal(value)


class RegistrySettings(BaseModel):
    """The shared services of a runtime process: the registry, its trust anchors and the two key holders."""

    model_config = ConfigDict(frozen=True)

    registry_url: str | None
    registry_api_key: SecretStr | None
    member_id: uuid.UUID | None
    public_keys: tuple[tuple[str, str], ...] | None
    key_holder_1_url: str | None
    key_holder_1_api_key: SecretStr | None
    key_holder_2_url: str | None
    key_holder_2_api_key: SecretStr | None

    @classmethod
    def read(cls, read: SettingReader) -> Self:
        """The variables that are present; requirements are checked per use."""
        return cls(
            registry_url=read.http_url("REGISTRY_URL"),
            registry_api_key=read.secret("REGISTRY_API_KEY"),
            member_id=read.member_id("REGISTRY_MEMBER_ID"),
            public_keys=read.public_keys("REGISTRY_PUBLIC_KEYS"),
            key_holder_1_url=read.http_url("KEY_HOLDER_1_URL"),
            key_holder_1_api_key=read.secret("KEY_HOLDER_1_API_KEY"),
            key_holder_2_url=read.http_url("KEY_HOLDER_2_URL"),
            key_holder_2_api_key=read.secret("KEY_HOLDER_2_API_KEY"),
        )

    def require_registry(self) -> tuple[str, str]:
        """REGISTRY_URL and REGISTRY_API_KEY."""
        return (
            _required("REGISTRY_URL", self.registry_url, str),
            _required("REGISTRY_API_KEY", self.registry_api_key, _reveal),
        )

    def require_member_id(self) -> uuid.UUID:
        """REGISTRY_MEMBER_ID."""
        if self.member_id is None:
            raise _missing("REGISTRY_MEMBER_ID")
        return self.member_id

    def require_public_keys(self) -> dict[str, str]:
        """REGISTRY_PUBLIC_KEYS, the only trust anchors of registry signatures (SPEC 15.1)."""
        if self.public_keys is None:
            raise _missing("REGISTRY_PUBLIC_KEYS")
        return dict(self.public_keys)

    def require_key_holder(self, number: int) -> tuple[str, str]:
        """KEY_HOLDER_<n>_URL and KEY_HOLDER_<n>_API_KEY for holder 1 or 2."""
        url, key = (
            (self.key_holder_1_url, self.key_holder_1_api_key)
            if number == 1
            else (self.key_holder_2_url, self.key_holder_2_api_key)
        )
        return (
            _required(f"KEY_HOLDER_{number}_URL", url, str),
            _required(f"KEY_HOLDER_{number}_API_KEY", key, _reveal),
        )


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
    registry: RegistrySettings
    log_level: str
    tunables: Tunables

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """Parse the variables of a runtime process that are present; requirements are checked per use."""
        read = SettingReader(environ)
        port_text = read.text("PORT")
        return cls(
            bank_code=_bank_code(read),
            bank_name=read.text("BANK_NAME"),
            bind_host=read.bind_host(),
            port=read.integer("PORT", 0, 1, MAX_PORT) if port_text is not None else None,
            database_url=read.secret("DATABASE_URL"),
            outside_records_url=read.http_url("OUTSIDE_RECORDS_URL"),
            outside_records_api_key=read.secret("OUTSIDE_RECORDS_API_KEY"),
            registry=RegistrySettings.read(read),
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
        read = SettingReader(environ)
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
