"""`SettingReader`: reads and validates one environment variable at a time (SPEC 3.1, SPEC_DECISIONS 37, 66).

Every value is trimmed of ASCII space, tab, CR and LF at both ends and must then hold no
control character and no whitespace other than the ASCII space. Numbers use the one
integer rule of decision 37; `BIND_HOST` is an IP literal or a wildcard. A malformed
value raises one sentence naming the variable, never its value. The reader is given a
mapping; only `settings.py` reads the process environment.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
import unicodedata
import uuid
from collections.abc import Mapping
from types import MappingProxyType
from urllib.parse import urlsplit

from pydantic import SecretStr

from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.text_rules import ascii_lower, parse_integer, trim
from onepledge_bank_agent.infrastructure.operator_errors import CONFIGURATION_ERROR, OperatorError

_BOOLEANS = MappingProxyType({"true": True, "false": False})
MAX_SECONDS = 3600.0
_KEY_ID = re.compile(r"^[a-z0-9-]{1,64}$")
_SECONDS = re.compile(r"^([0-9]{1,6})(?:\.([0-9]{1,3}))?$")
PUBLIC_KEY_BYTES = 32
# The published test vector key (contracts/examples/vectors/signatures.json) is never a trust anchor.
TEST_VECTOR_PUBLIC_KEYS = frozenset({"BlflaowBg3VkeFNDcT9cKZFMgLkwYA7l+SNUMxWPOVQ="})

DEFAULT_BIND_HOST = "127.0.0.1"
BIND_HOST_RULE = "an IP address of this machine; set BIND_HOST to 127.0.0.1 or 0.0.0.0"


def _unprintable(char: str) -> bool:
    """A control character, or whitespace that is not the ASCII space."""
    return unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") or (
        unicodedata.category(char) == "Zs" and char != " "
    )


def invalid(name: str, rule: str) -> OperatorError:
    """The one sentence of a malformed setting: the variable and the rule, never the value."""
    return OperatorError(f"Configuration error: {name} must be {rule}.", CONFIGURATION_ERROR)


class SettingReader:
    """Reads and validates one variable at a time."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    def text(self, name: str) -> str | None:
        """The value trimmed of ASCII space, tab, CR and LF at both ends (SPEC_DECISIONS 66).

        A value that still holds a control character or any whitespace other than the
        ASCII space is malformed and names its variable, secrets included.
        """
        value = trim(self._environ.get(name, ""))
        if any(_unprintable(char) for char in value):
            raise invalid(name, "free of control characters and of whitespace other than spaces")
        return value or None

    def bind_host(self) -> str:
        """`BIND_HOST`: an IPv4 or IPv6 literal, or the wildcard `0.0.0.0` or `::`; a name is refused."""
        value = self.text("BIND_HOST") or DEFAULT_BIND_HOST
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            raise invalid("BIND_HOST", BIND_HOST_RULE) from None

    def secret(self, name: str) -> SecretStr | None:
        """A value masked in every repr."""
        value = self.text(name)
        return SecretStr(value) if value is not None else None

    def matching(self, name: str, pattern: re.Pattern[str], rule: str) -> str | None:
        """A value that must match `pattern`."""
        value = self.text(name)
        if value is not None and not pattern.fullmatch(value):
            raise invalid(name, rule)
        return value

    def integer(self, name: str, default: int, low: int, high: int) -> int:
        """A whole number by the integer rule of SPEC_DECISIONS 37, within `low` and `high`."""
        value = self.text(name)
        if value is None:
            return default
        try:
            return parse_integer(value, name, low, high)
        except InvalidValueError:
            raise invalid(name, f"a whole number from {low} to {high}") from None

    def seconds(self, name: str, default: float) -> float:
        """A positive number of seconds with at most three decimals, at most an hour."""
        value = self.text(name)
        if value is None:
            return default
        match = _SECONDS.fullmatch(value)
        if match is None:
            raise invalid(name, "a positive number of seconds")
        fraction = (match[2] or "").ljust(3, "0")
        milliseconds = parse_integer(match[1], name, 0) * 1000 + parse_integer(fraction, name, 0)
        seconds = milliseconds / 1000
        if not 0 < seconds <= MAX_SECONDS:
            raise invalid(name, "a positive number of seconds")
        return seconds

    def boolean(self, name: str, default: bool) -> bool:
        """`true` or `false`, in any case."""
        value = self.text(name)
        if value is None:
            return default
        if ascii_lower(value) not in _BOOLEANS:
            raise invalid(name, "true or false")
        return _BOOLEANS[ascii_lower(value)]

    def member_id(self, name: str) -> uuid.UUID | None:
        """A UUID in its canonical lower case form."""
        value = self.text(name)
        if value is None:
            return None
        try:
            parsed = uuid.UUID(value)
        except ValueError:
            raise invalid(name, "a UUID") from None
        if str(parsed) != ascii_lower(value):
            raise invalid(name, "a UUID")
        return parsed

    def public_keys(self, name: str) -> tuple[tuple[str, str], ...] | None:
        """A JSON object of key ids to base64 Ed25519 public keys, the test vector key refused."""
        value = self.text(name)
        if value is None:
            return None
        rule = "a JSON object of key ids to base64 Ed25519 public keys"
        try:
            document = json.loads(value)
        except ValueError:
            raise invalid(name, rule) from None
        if not isinstance(document, dict) or not document:
            raise invalid(name, rule)
        keys: list[tuple[str, str]] = []
        for key_id, key in sorted(document.items()):
            if not isinstance(key, str) or not _KEY_ID.fullmatch(key_id) or not _is_public_key(key):
                raise invalid(name, rule)
            if key in TEST_VECTOR_PUBLIC_KEYS:
                raise invalid(name, "a real registry key, not the published test vector key")
            keys.append((key_id, key))
        return tuple(keys)

    def http_url(self, name: str) -> str | None:
        """An http or https URL with a host."""
        value = self.text(name)
        if value is not None:
            parts = urlsplit(value)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise invalid(name, "an http or https URL")
        return value


def _is_public_key(text: str) -> bool:
    try:
        return len(base64.b64decode(text, validate=True)) == PUBLIC_KEY_BYTES
    except (binascii.Error, ValueError):
        return False
