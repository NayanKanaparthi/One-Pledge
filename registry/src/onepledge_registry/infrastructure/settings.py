"""Settings of the registry, read from the environment. The only module that reads it.

Each value is trimmed of ASCII space, tab, CR and LF at both ends; an empty value counts as
absent. Every refusal is one sentence that names the variable and never repeats its value.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

# SPEC section 3: the registry listens on 8400 unless PORT says otherwise.
DEFAULT_PORT = 8400
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


def read_port(environ: Mapping[str, str]) -> int:
    """PORT: 1 to 65535 in ASCII digits, or DEFAULT_PORT when absent."""
    text = _value(environ, "PORT")
    if text is None:
        return DEFAULT_PORT
    number = int(text) if _DIGITS.fullmatch(text) else None
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
    """What `serve` needs: where to listen."""

    bind_host: str
    port: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> ServeSettings:
        """Read and check every variable of `serve`."""
        return cls(bind_host=read_bind_host(environ), port=read_port(environ))


def load_serve_settings() -> ServeSettings:
    """The settings of `serve` from the process environment."""
    return ServeSettings.from_env(os.environ)


def load_migration_url() -> str:
    """MIGRATION_DATABASE_URL, the owner role's URL, for the Alembic environment."""
    value = _value(os.environ, "MIGRATION_DATABASE_URL")
    if value is None:
        raise ConfigurationError("MIGRATION_DATABASE_URL", "is required")
    return value
