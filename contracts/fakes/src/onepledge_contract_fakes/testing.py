"""Deterministic collaborators for tests that use the doubles in-process (TEST DOUBLES).

A settable clock, counting UUIDs, API key generation, ready-made configurations
and an in-process HTTP client, so the doubles' own tests and other layers' tests
build identical setups.
"""

from __future__ import annotations

import asyncio
import json as _json
import secrets
import threading
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Self

import httpx
import oprf
from starlette.types import ASGIApp

from onepledge_contract_fakes.key_holder.config import KeyHolderConfig
from onepledge_contract_fakes.members import api_key_sha256
from onepledge_contract_fakes.registry.config import RegistryConfig
from onepledge_contract_fakes.settings import MemberCredential

API_KEY_PREFIX = "opk_"


class FixedClock:
    """A clock that only moves when told to."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        """The current fixed time."""
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        with self._lock:
            self._now += timedelta(seconds=seconds)


class CountingIds:
    """UUID version 4 shaped identifiers from a counter, unique and repeatable."""

    def __init__(self, namespace: int = 0) -> None:
        self._next = 1
        self._namespace = namespace
        self._lock = threading.Lock()

    def new_id(self) -> str:
        """The next identifier."""
        with self._lock:
            value = (self._namespace << 64) | self._next
            self._next += 1
        return str(uuid.UUID(int=value, version=4))


def new_api_key() -> str:
    """A fresh service key in the `opk_` plus 43 url-safe characters format."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def new_members(count: int) -> tuple[tuple[MemberCredential, ...], tuple[str, ...]]:
    """`count` members with random ids, and their API keys in the same order."""
    keys = tuple(new_api_key() for _ in range(count))
    members = tuple(MemberCredential(str(uuid.uuid4()), api_key_sha256(key)) for key in keys)
    return members, keys


def new_scalar() -> bytes:
    """A fresh random 32 byte ristretto255 scalar."""
    return bytes(oprf.mask.random())


def key_holder_config(
    holder_id: str = "kh1",
    members: tuple[MemberCredential, ...] = (),
    scalar: bytes | None = None,
    daily_element_cap: int = 1_000_000,
    test_control_key: str | None = None,
) -> KeyHolderConfig:
    """A key holder configuration with one active key version 1."""
    return KeyHolderConfig(
        holder_id=holder_id,
        scalars={1: scalar if scalar is not None else new_scalar()},
        active_version=1,
        members=members,
        daily_element_cap=daily_element_cap,
        test_control_key=test_control_key,
    )


def registry_config(
    members: tuple[MemberCredential, ...],
    auditor_key: str,
    daily_item_cap: int = 1_000_000,
    test_control_key: str | None = None,
) -> RegistryConfig:
    """A registry configuration with a fresh signing seed and key version 1.1."""
    return RegistryConfig(
        members=members,
        signing_seed=secrets.token_bytes(32),
        signing_key_id="registry-test-1",
        auditor_key_sha256=api_key_sha256(auditor_key),
        key_version="1.1",
        daily_item_cap=daily_item_cap,
        test_control_key=test_control_key,
    )


class AsgiTestClient:
    """A synchronous HTTP client that calls an ASGI app in-process through httpx."""

    def __init__(self, app: ASGIApp, base_url: str = "http://testserver") -> None:
        self._runner = asyncio.Runner()
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        self._client = httpx.AsyncClient(transport=transport, base_url=base_url)

    def send(
        self, method: str, url: str, content: bytes | None = None, headers: Mapping[str, str] | None = None
    ) -> httpx.Response:
        """Send one request and return the complete response. Query strings go in `url`."""
        return self._runner.run(self._client.request(method, url, content=content, headers=headers))

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> httpx.Response:
        """A GET request."""
        return self.send("GET", url, headers=headers)

    def post(self, url: str, json: object = None, headers: Mapping[str, str] | None = None) -> httpx.Response:
        """A POST request with a JSON body (no body when `json` is None)."""
        content = None if json is None else _json.dumps(json).encode("utf-8")
        merged = {"content-type": "application/json", **(headers or {})}
        return self.send("POST", url, content=content, headers=merged)

    def post_raw(self, url: str, content: bytes, headers: Mapping[str, str] | None = None) -> httpx.Response:
        """A POST request with raw body bytes."""
        return self.send("POST", url, content=content, headers=headers)

    def close(self) -> None:
        """Close the client and its event loop."""
        self._runner.run(self._client.aclose())
        self._runner.close()

    def __enter__(self) -> Self:
        """Use as a context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close on exit."""
        self.close()
