"""`ServiceClients`: one pooled HTTP client per dependency of a runtime process, closed together (SPEC 16.6).

Each dependency has its own key, its own timeouts and its own client; a client is made
on first use, so a command that never calls a service never needs its variables.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from onepledge_bank_agent.application.ports import IdGenerator, OperationalLog
from onepledge_bank_agent.domain.dependency_errors import KEY_HOLDER_1, KEY_HOLDER_2, REGISTRY
from onepledge_bank_agent.domain.errors import OUTSIDE_RECORDS
from onepledge_bank_agent.infrastructure.http.base import (
    CallPolicy,
    HttpClientFactory,
    HttpTimeouts,
    ResponseBounds,
    RetryingHttpCaller,
)
from onepledge_bank_agent.infrastructure.http.key_holder_client import KeyHolderHttpClient
from onepledge_bank_agent.infrastructure.http.outside_records import OutsideRecordsClient
from onepledge_bank_agent.infrastructure.http.registry_client import RegistryHttpClient
from onepledge_bank_agent.infrastructure.settings import RuntimeSettings


@dataclass(frozen=True)
class ClientParts:
    """How HTTP clients are made in this process, and the collaborators every caller shares."""

    factory: HttpClientFactory
    log: OperationalLog
    sleep: Callable[[float], None]
    ids: IdGenerator


class ServiceClients:
    """Builds and keeps the outside records, registry and key holder clients of one process."""

    def __init__(self, settings: RuntimeSettings, parts: ClientParts) -> None:
        self._settings = settings
        self._parts = parts
        self._callers: dict[str, RetryingHttpCaller] = {}

    def _caller(self, dependency: str, url: str, key: str) -> RetryingHttpCaller:
        caller = self._callers.get(dependency)
        if caller is None:
            tunables = self._settings.tunables
            timeouts = HttpTimeouts(tunables.http_connect_timeout_seconds, tunables.http_read_timeout_seconds)
            client = self._parts.factory(url, timeouts)
            bounds = ResponseBounds(tunables.http_max_response_bytes, tunables.http_total_timeout_seconds)
            caller = RetryingHttpCaller(
                client, key, dependency, self._parts.log, CallPolicy(self._parts.sleep, bounds)
            )
            self._callers[dependency] = caller
        return caller

    def outside_records(self) -> OutsideRecordsClient:
        """Titles, payments and contracts."""
        url, key = self._settings.require_outside_records()
        return OutsideRecordsClient(self._caller(OUTSIDE_RECORDS, url, key))

    def registry(self) -> RegistryHttpClient:
        """The registry."""
        url, key = self._settings.registry.require_registry()
        return RegistryHttpClient(self._caller(REGISTRY, url, key))

    def key_holder(self, number: int) -> KeyHolderHttpClient:
        """Key holder 1 or 2."""
        url, key = self._settings.registry.require_key_holder(number)
        dependency = KEY_HOLDER_1 if number == 1 else KEY_HOLDER_2
        return KeyHolderHttpClient(self._caller(dependency, url, key), dependency, self._parts.ids)

    def close(self) -> None:
        """Close every pooled connection."""
        for caller in self._callers.values():
            caller.close()
        self._callers.clear()
