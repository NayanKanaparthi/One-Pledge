"""Settings of the key holder, read from the environment. The only module that reads it.

Each value is trimmed of ASCII space, tab, CR and LF at both ends; an empty value counts as
absent. Every refusal is one sentence that names the variable and never repeats its value.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from onepledge_key_holder.domain.elements import is_usable_scalar
from onepledge_key_holder.domain.identity import (
    MAX_KEY_VERSION,
    HolderId,
    InvalidKeyVersionError,
    KeyVersion,
)

# SPEC section 3: kh1 listens on 8301 and kh2 on 8302 unless PORT says otherwise.
DEFAULT_PORTS = {HolderId.KH1: 8301, HolderId.KH2: 8302}
DEFAULT_BIND_HOST = "127.0.0.1"
MAX_PORT = 65_535
# The layer 1 double's default (decision 11); the SPEC gives no number.
DEFAULT_DAILY_ELEMENT_CAP = 1_000_000
MAX_DAILY_ELEMENT_CAP = 2_147_483_647
_DATABASE_SCHEMES = frozenset({"postgresql", "postgresql+psycopg"})
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_DIGITS = re.compile(r"[0-9]{1,10}")
_TRIMMED = " \t\r\n"


class ConfigurationError(Exception):
    """A variable is missing or malformed. `sentence` names the variable, never its value."""

    def __init__(self, variable: str, problem: str) -> None:
        self.variable = variable
        self.sentence = f"{variable} {problem}."
        super().__init__(self.sentence)


def _value(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip(_TRIMMED)
    return value or None


def _required(environ: Mapping[str, str], name: str) -> str:
    value = _value(environ, name)
    if value is None:
        raise ConfigurationError(name, "is required")
    return value


def _ascii_integer(text: str) -> int | None:
    """The integer of 1 to 10 ASCII digits, or None for any other text (`+5`, `1.0`, other digits)."""
    return int(text) if _DIGITS.fullmatch(text) else None


def read_holder_id(environ: Mapping[str, str]) -> HolderId:
    """HOLDER_ID: `kh1` or `kh2`."""
    text = _required(environ, "HOLDER_ID")
    if text not in {holder.value for holder in HolderId}:
        raise ConfigurationError("HOLDER_ID", "must be kh1 or kh2")
    return HolderId(text)


def read_key_version(environ: Mapping[str, str]) -> KeyVersion:
    """ACTIVE_KEY_VERSION: the key version this holder evaluates with, 1 to MAX_KEY_VERSION."""
    problem = f"must be a whole number from 1 to {MAX_KEY_VERSION:,} in ASCII digits"
    number = _ascii_integer(_required(environ, "ACTIVE_KEY_VERSION"))
    if number is None:
        raise ConfigurationError("ACTIVE_KEY_VERSION", problem)
    try:
        return KeyVersion(number)
    except InvalidKeyVersionError:
        raise ConfigurationError("ACTIVE_KEY_VERSION", problem) from None


def read_port(environ: Mapping[str, str], default: int) -> int:
    """PORT: 1 to 65535 in ASCII digits, or `default` when absent."""
    text = _value(environ, "PORT")
    if text is None:
        return default
    number = _ascii_integer(text)
    if number is None or not 1 <= number <= MAX_PORT:
        raise ConfigurationError("PORT", f"must be a whole number from 1 to {MAX_PORT} in ASCII digits")
    return number


def read_bind_host(environ: Mapping[str, str]) -> str:
    """BIND_HOST: an IPv4 or IPv6 literal (a name such as `localhost` is refused), default 127.0.0.1."""
    text = _value(environ, "BIND_HOST")
    if text is None:
        return DEFAULT_BIND_HOST
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        raise ConfigurationError(
            "BIND_HOST", "must be an IPv4 or IPv6 literal, for example 127.0.0.1 or 0.0.0.0"
        ) from None


def read_scalar(environ: Mapping[str, str]) -> bytes:
    """OPRF_SCALAR_B64: the standard base64 of this holder's 32 byte scalar, not all zeros (SPEC 4.2)."""
    text = _required(environ, "OPRF_SCALAR_B64")
    try:
        raw = _canonical_base64(text)
    except ValueError:
        raw = b""
    if not is_usable_scalar(raw):
        raise ConfigurationError(
            "OPRF_SCALAR_B64", "must be the standard base64 of 32 bytes that are not all zeros"
        )
    return raw


def _canonical_base64(text: str) -> bytes:
    """Standard base64 that re-encodes to the same text, or ValueError."""
    raw = base64.b64decode(text, validate=True)
    if base64.b64encode(raw).decode("ascii") != text:
        raise ValueError
    return raw


def read_database_url(environ: Mapping[str, str], name: str, needs_database: bool = True) -> str:
    """A `postgresql://USER:PASSWORD@HOST:PORT/DATABASE` URL. The value is a secret and is never echoed."""
    text = _required(environ, name)
    try:
        url = make_url(text)
    except (ArgumentError, ValueError):
        url = None
    if (
        url is None
        or url.drivername not in _DATABASE_SCHEMES
        or (needs_database and not url.database)
        or url.query
    ):
        raise ConfigurationError(
            name, "must be a postgresql://USER:PASSWORD@HOST:PORT/DATABASE URL with no query parameters"
        )
    return text


def read_daily_element_cap(environ: Mapping[str, str]) -> int:
    """DAILY_ELEMENT_CAP: elements one member may send per UTC day, default 1,000,000."""
    text = _value(environ, "DAILY_ELEMENT_CAP")
    if text is None:
        return DEFAULT_DAILY_ELEMENT_CAP
    number = _ascii_integer(text)
    if number is None or not 1 <= number <= MAX_DAILY_ELEMENT_CAP:
        raise ConfigurationError(
            "DAILY_ELEMENT_CAP",
            f"must be a whole number from 1 to {MAX_DAILY_ELEMENT_CAP:,} in ASCII digits",
        )
    return number


class _RepeatedKeyError(ValueError):
    """A JSON object names the same key twice."""


def _object_without_repeats(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise _RepeatedKeyError
        document[key] = value
    return document


def _is_canonical_uuid(text: str) -> bool:
    try:
        return str(uuid.UUID(text)) == text
    except ValueError:
        return False


def read_member_key_hashes(environ: Mapping[str, str]) -> Mapping[str, str]:
    """MEMBER_KEY_HASHES: a JSON object of member id to the SHA-256 hex of that member's API key.

    The holder is never given a key itself. A member id is a lower case UUID, a hash is 64
    lower case hexadecimal characters, and no id or hash appears twice.
    """
    name = "MEMBER_KEY_HASHES"
    problem = (
        "must be a JSON object of member id (a lower case UUID) to the SHA-256 of its API key "
        "(64 lower case hexadecimal characters), with no id or hash repeated"
    )
    try:
        document = json.loads(_required(environ, name), object_pairs_hook=_object_without_repeats)
    except (ValueError, RecursionError):
        raise ConfigurationError(name, problem) from None
    if not isinstance(document, dict):
        raise ConfigurationError(name, problem)
    hashes: dict[str, str] = {}
    for member_id, key_hash in document.items():
        if not _is_canonical_uuid(member_id):
            raise ConfigurationError(name, problem)
        if not isinstance(key_hash, str) or not _SHA256_HEX.fullmatch(key_hash):
            raise ConfigurationError(name, problem)
        hashes[member_id] = key_hash
    if len(set(hashes.values())) != len(hashes):
        raise ConfigurationError(name, problem)
    return MappingProxyType(hashes)


@dataclass(frozen=True)
class ServeSettings:
    """What `serve` needs: the identity, the key version and its scalar, the database, where to listen.

    The scalar and the database URL are secrets and never appear in the `repr`.
    """

    holder_id: HolderId
    active_key_version: KeyVersion
    bind_host: str
    port: int
    database_url: str = field(repr=False)
    scalar: bytes = field(repr=False)
    daily_element_cap: int = DEFAULT_DAILY_ELEMENT_CAP

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> ServeSettings:
        """Read and check every variable of `serve`, in the order the README lists them."""
        holder_id = read_holder_id(environ)
        return cls(
            holder_id=holder_id,
            active_key_version=read_key_version(environ),
            bind_host=read_bind_host(environ),
            port=read_port(environ, DEFAULT_PORTS[holder_id]),
            database_url=read_database_url(environ, "DATABASE_URL"),
            scalar=read_scalar(environ),
            daily_element_cap=read_daily_element_cap(environ),
        )


@dataclass(frozen=True)
class ProvisionSettings:
    """What `db provision` needs: the superuser URL and the two role URLs of this holder's database."""

    admin_url: str = field(repr=False)
    migration_url: str = field(repr=False)
    database_url: str = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> ProvisionSettings:
        """Read PG_ADMIN_URL, MIGRATION_DATABASE_URL and DATABASE_URL."""
        return cls(
            admin_url=read_database_url(environ, "PG_ADMIN_URL", needs_database=False),
            migration_url=read_database_url(environ, "MIGRATION_DATABASE_URL"),
            database_url=read_database_url(environ, "DATABASE_URL"),
        )


@dataclass(frozen=True)
class MigrateSettings:
    """What `db migrate` needs: the owner URL, the active key version and the member key hashes."""

    migration_url: str = field(repr=False)
    active_key_version: KeyVersion
    member_key_hashes: Mapping[str, str]

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> MigrateSettings:
        """Read MIGRATION_DATABASE_URL, ACTIVE_KEY_VERSION and MEMBER_KEY_HASHES."""
        return cls(
            migration_url=read_database_url(environ, "MIGRATION_DATABASE_URL"),
            active_key_version=read_key_version(environ),
            member_key_hashes=read_member_key_hashes(environ),
        )


def load_serve_settings() -> ServeSettings:
    """The settings of `serve` from the process environment."""
    return ServeSettings.from_env(os.environ)


def load_provision_settings() -> ProvisionSettings:
    """The settings of `db provision` from the process environment."""
    return ProvisionSettings.from_env(os.environ)


def load_migrate_settings() -> MigrateSettings:
    """The settings of `db migrate` from the process environment."""
    return MigrateSettings.from_env(os.environ)


def load_migration_url() -> str:
    """MIGRATION_DATABASE_URL, the owner role's URL, for the Alembic environment."""
    return _required(os.environ, "MIGRATION_DATABASE_URL")
