"""Clients of the shared services over a scripted network boundary, for unit tests.

`caller` builds the product's own `RetryingHttpCaller` on an `httpx.MockTransport`, so a
test decides every answer and sees every request, and the client under test is the real
one. Retries wait for nothing.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from onepledge_bank_agent.domain.registry_records import OutboxMessage, OutboxPage
from onepledge_bank_agent.infrastructure.http.base import CallPolicy, RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.registry_client import RegistryHttpClient

type Handler = Callable[[httpx.Request], httpx.Response]


class Quiet:
    """An `OperationalLog` that writes nothing."""

    def info(self, event: str, **fields: object) -> None:
        """Nothing."""

    def warning(self, event: str, **fields: object) -> None:
        """Nothing."""

    def failure(self, event: str, error: BaseException, **fields: object) -> None:
        """Nothing."""


def caller(handler: Handler, dependency: str, seen: list[httpx.Request]) -> RetryingHttpCaller:
    """A caller whose every request is recorded in `seen` and answered by `handler`."""

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(base_url="http://service.test", transport=httpx.MockTransport(record))
    return RetryingHttpCaller(client, "k" * 40, dependency, Quiet(), CallPolicy(lambda _: None))


def registry(handler: Handler, seen: list[httpx.Request] | None = None) -> RegistryHttpClient:
    """The registry client answered by `handler`."""
    return RegistryHttpClient(caller(handler, "registry", seen if seen is not None else []))


def messages(page: OutboxPage) -> list[OutboxMessage]:
    """The elements of a page, each of which must have had a usable id."""
    found = [element for element in page.elements if isinstance(element, OutboxMessage)]
    assert len(found) == len(page.elements), "an element had no usable id"
    return found
