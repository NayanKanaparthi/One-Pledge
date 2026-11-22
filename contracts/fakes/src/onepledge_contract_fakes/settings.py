"""Environment access and parsing helpers for the test doubles (TEST DOUBLES, SPEC 3.1).

This is the only module of the package that reads `os.environ`. Each double has
its own immutable settings class (`key_holder.settings`, `registry.settings`) built
from a mapping through `EnvironmentReader`, so tests pass a plain dict, and its own
`load_settings()` that reads the process environment through `read_environment()`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import os
import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from onepledge_contract_fakes.errors import ConfigurationError
from onepledge_contract_fakes.json_body import MalformedJsonError, parse_bounded_json
from onepledge_contract_fakes.validation import MAX_INTEGER, Issue, parse_integer_text

# SHA-256 of the raw bytes of every secret published in contracts/examples/vectors/.
# A double refuses to start with any of them (SPEC 16.7). The values themselves are
# kept only in the vector files.
PUBLISHED_TEST_VECTOR_SECRET_SHA256: frozenset[str] = frozenset(
    {
        "f51c3d600a3a5502cbde47eeff8b5057b211a2637f2c76668c2bf6ad27252446",
        "0b2a8b57a9ebbf2edb162aac1ffc6d4f28634a60bacaaa62be722c2404810440",
        "1c5ad9bdac7407a0fba0502d543d2219c525ccadff7713a3f708b2115c204f38",
        "22597df3e4412c02b6f5dbcc977704ab0b911f041ee729d642d0b7eed36d32df",
    }
)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
MIN_TEST_CONTROL_KEY_LENGTH = 32
DEFAULT_BIND_HOST = "127.0.0.1"
# Settings are trimmed of ASCII whitespace only; any other space is part of the value and fails it.
ASCII_WHITESPACE = " \t\r\n"
SECRET_BYTES = 32  # ristretto255 scalars and Ed25519 seeds


def read_environment() -> Mapping[str, str]:
    """The process environment. The one place the package touches `os.environ`."""
    return os.environ


@dataclass(frozen=True)
class MemberCredential:
    """A registry member id and the SHA-256 of its API key."""

    member_id: str
    api_key_sha256: str


class EnvironmentReader:
    """Typed, validating access to environment variables. Errors name the variable only."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    def _raw(self, name: str) -> str:
        """The value trimmed of ASCII whitespace; anything else that is not plain text is refused.

        SPEC_DECISIONS 66: no whitespace other than a space, and no control character, may stay
        in a value once its ends are trimmed. This holds for secrets too.
        """
        value = self._environ.get(name, "").strip(ASCII_WHITESPACE)
        if any(_unplain(char) for char in value):
            raise ConfigurationError(name, "contains whitespace other than a space, or a control character")
        return value

    def required(self, name: str) -> str:
        """A variable that must be present and not empty."""
        value = self._raw(name)
        if not value:
            raise ConfigurationError(name, "is required but not set")
        return value

    def optional(self, name: str, default: str) -> str:
        """A variable with a default used when it is absent or empty."""
        value = self._raw(name)
        return value or default

    def integer(
        self, name: str, default: int | None = None, minimum: int = 0, maximum: int | None = None
    ) -> int:
        """An integer variable inside a range."""
        raw = self._raw(name)
        if not raw:
            if default is None:
                raise ConfigurationError(name, "is required but not set")
            return default
        return parse_setting_integer(name, raw, minimum, MAX_INTEGER if maximum is None else maximum)

    def port(self, name: str = "PORT") -> int:
        """A TCP port number."""
        return self.integer(name, minimum=1, maximum=65535)

    def bind_host(self, name: str = "BIND_HOST") -> str:
        """An IPv4 or IPv6 address literal to listen on, default 127.0.0.1 (SPEC 3.1)."""
        value = self.optional(name, DEFAULT_BIND_HOST)
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            raise ConfigurationError(
                name, "must be an IP address of this machine; set BIND_HOST to 127.0.0.1 or 0.0.0.0"
            ) from None

    def boolean(self, name: str, default: bool) -> bool:
        """A `true` or `false` variable."""
        raw = self._raw(name).lower()
        if not raw:
            return default
        if raw not in ("true", "false"):
            raise ConfigurationError(name, "must be true or false")
        return raw == "true"

    def log_level(self) -> str:
        """LOG_LEVEL, default INFO."""
        level = self.optional("LOG_LEVEL", "INFO").upper()
        if level not in _LOG_LEVELS:
            raise ConfigurationError("LOG_LEVEL", "must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        return level

    def json_value(self, name: str) -> object:
        """A required variable holding JSON."""
        raw = self.required(name)
        try:
            return parse_bounded_json(raw)
        except MalformedJsonError:
            raise ConfigurationError(name, "must be valid JSON without duplicate keys") from None

    def sha256_hex(self, name: str) -> str:
        """A required lower case SHA-256 hex digest."""
        value = self.required(name)
        if not _SHA256_HEX.fullmatch(value):
            raise ConfigurationError(name, "must be 64 lower case hex characters")
        return value

    def secret_base64_32(self, name: str) -> bytes:
        """A required base64 secret of exactly 32 bytes that is not a published test vector."""
        raw = self.required(name)
        decoded = decode_base64_32(raw)
        if decoded is None:
            raise ConfigurationError(name, "must be standard base64 of exactly 32 bytes")
        reject_published_vector(name, decoded)
        return decoded

    def test_control_key(self, enabled: bool) -> str | None:
        """FAKE_TEST_CONTROL_KEY, required with a minimum length when test controls are on."""
        if not enabled:
            return None
        key = self.required("FAKE_TEST_CONTROL_KEY")
        if len(key) < MIN_TEST_CONTROL_KEY_LENGTH:
            raise ConfigurationError("FAKE_TEST_CONTROL_KEY", "must be at least 32 characters long")
        return key

    def members(self, name: str) -> tuple[MemberCredential, ...]:
        """A JSON list of `{member_id, api_key_sha256}` objects with unique ids and hashes."""
        parsed = self.json_value(name)
        if not isinstance(parsed, list) or not parsed:
            raise ConfigurationError(name, "must be a non-empty JSON list")
        members = tuple(_parse_member(name, entry) for entry in parsed)
        if len({m.member_id for m in members}) != len(members):
            raise ConfigurationError(name, "contains a duplicate member_id")
        if len({m.api_key_sha256 for m in members}) != len(members):
            raise ConfigurationError(name, "contains a duplicate api_key_sha256")
        return members


def _unplain(char: str) -> bool:
    """Whitespace other than a space, or a control or format character (Unicode categories Cc, Cf)."""
    return (char.isspace() and char != " ") or unicodedata.category(char) in ("Cc", "Cf")


def parse_setting_integer(name: str, text: str, minimum: int, maximum: int) -> int:
    """An integer setting under the one integer rule of the package, naming the variable when it fails."""
    parsed = parse_integer_text(text, minimum, maximum)
    if parsed is Issue.INVALID_INTEGER:
        raise ConfigurationError(name, "must be a whole number written with the digits 0 to 9")
    if isinstance(parsed, Issue):
        raise ConfigurationError(name, f"must be between {minimum:,} and {maximum:,}")
    return parsed


def _parse_member(name: str, entry: object) -> MemberCredential:
    if not isinstance(entry, dict) or set(entry) != {"member_id", "api_key_sha256"}:
        raise ConfigurationError(name, "entries must be objects with member_id and api_key_sha256")
    member_id, key_hash = entry["member_id"], entry["api_key_sha256"]
    if not isinstance(member_id, str) or not _is_uuid(member_id):
        raise ConfigurationError(name, "has a member_id that is not a lower case UUID")
    if not isinstance(key_hash, str) or not _SHA256_HEX.fullmatch(key_hash):
        raise ConfigurationError(name, "has an api_key_sha256 that is not 64 lower case hex characters")
    return MemberCredential(member_id=member_id, api_key_sha256=key_hash)


def _is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def decode_base64_32(value: str) -> bytes | None:
    """Decode standard base64 that must hold exactly 32 bytes, or return None."""
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    return decoded if len(decoded) == SECRET_BYTES else None


def reject_published_vector(name: str, secret: bytes) -> None:
    """Refuse a secret that is one of the public test vector values."""
    if hashlib.sha256(secret).hexdigest() in PUBLISHED_TEST_VECTOR_SECRET_SHA256:
        raise ConfigurationError(name, "holds a published test vector value and must not be used as a secret")
