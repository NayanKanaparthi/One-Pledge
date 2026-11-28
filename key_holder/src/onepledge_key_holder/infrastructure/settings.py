"""Settings of the key holder, read from the environment. The only module that reads it.

Each value is trimmed of ASCII space, tab, CR and LF at both ends; an empty value counts as
absent. Every refusal is one sentence that names the variable and never repeats its value.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

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


@dataclass(frozen=True)
class ServeSettings:
    """What `serve` needs: the holder identity, its active key version and where to listen."""

    holder_id: HolderId
    active_key_version: KeyVersion
    bind_host: str
    port: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> ServeSettings:
        """Read and check every variable of `serve`, in the order the README lists them."""
        holder_id = read_holder_id(environ)
        return cls(
            holder_id=holder_id,
            active_key_version=read_key_version(environ),
            bind_host=read_bind_host(environ),
            port=read_port(environ, DEFAULT_PORTS[holder_id]),
        )


def load_serve_settings() -> ServeSettings:
    """The settings of `serve` from the process environment."""
    return ServeSettings.from_env(os.environ)


def load_migration_url() -> str:
    """MIGRATION_DATABASE_URL, the owner role's URL, for the Alembic environment."""
    return _required(os.environ, "MIGRATION_DATABASE_URL")
