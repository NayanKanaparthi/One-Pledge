"""The shared outbound HTTP client: timeouts, no redirects, pooling, bounded retries (SPEC 16.6).

Each call is retried up to two more times (after 0.2 s and 0.4 s) on a transport error,
a timeout, 502, 503 or 504. Every call the bank agent makes is idempotent by design: a
key holder evaluation is a pure function, a registry submission carries its
idempotency key, an acknowledgement and a repeated case position change nothing.
Only route templates are logged, never a path or a query string, because outside
records carry the VIN in both.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import OperationalLog
from onepledge_bank_agent.infrastructure.request_context import current_request_id

RETRY_DELAYS_SECONDS = (0.2, 0.4)
RETRY_STATUSES = frozenset({502, 503, 504})


type JsonBody = Mapping[str, object]


@dataclass(frozen=True)
class OutboundRequest:
    """One call: method, route template (the only form ever logged), path, and query or JSON body."""

    method: str
    route: str
    path: str
    params: Mapping[str, str | int] | None = None
    body: JsonBody | None = None
    headers: Mapping[str, str] | None = None


@dataclass(frozen=True)
class HttpTimeouts:
    """Connect and read timeouts of one dependency."""

    connect_seconds: float
    read_seconds: float


type HttpClientFactory = Callable[[str, HttpTimeouts], httpx.Client]


def make_http_client(base_url: str, timeouts: HttpTimeouts) -> httpx.Client:
    """One pooled client per dependency, with timeouts and no redirects."""
    timeout = httpx.Timeout(
        connect=timeouts.connect_seconds,
        read=timeouts.read_seconds,
        write=timeouts.read_seconds,
        pool=timeouts.connect_seconds,
    )
    return httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=False)


class RetryingHttpCaller:
    """Sends idempotent requests to one dependency with the retry rule above."""

    def __init__(
        self,
        client: httpx.Client,
        api_key: str,
        dependency: str,
        log: OperationalLog,
        sleep: Callable[[float], None],
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._dependency = dependency
        self._log = log
        self._sleep = sleep

    def get(
        self, route: str, path: str, params: Mapping[str, str | int] | None = None
    ) -> httpx.Response | None:
        """GET `path`, logged as `route`; the last response, or None when the service stayed unreachable."""
        return self.send(OutboundRequest("GET", route, path, params=params))

    def post(
        self, route: str, path: str, body: JsonBody | None = None, headers: Mapping[str, str] | None = None
    ) -> httpx.Response | None:
        """POST a JSON body to `path`, logged as `route`; as `get`."""
        return self.send(OutboundRequest("POST", route, path, body=body, headers=headers))

    def send(self, outbound: OutboundRequest) -> httpx.Response | None:
        """Send with the retry rule; the last response, or None when every attempt failed in transport."""
        headers = {"X-API-Key": self._api_key, "Accept": "application/json", **dict(outbound.headers or {})}
        request_id = current_request_id()
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        attempts = 1 + len(RETRY_DELAYS_SECONDS)
        for attempt in range(1, attempts + 1):
            response: httpx.Response | None
            try:
                response = self._client.request(
                    outbound.method,
                    outbound.path,
                    params=outbound.params,
                    json=outbound.body,
                    headers=headers,
                )
            except httpx.TransportError:
                response = None
            if response is not None and response.status_code not in RETRY_STATUSES:
                return response
            status = response.status_code if response is not None else None
            if attempt == attempts:
                self._log.warning(
                    LogEvent.DEPENDENCY_UNAVAILABLE,
                    dependency=self._dependency,
                    method=outbound.method,
                    route=outbound.route,
                    attempts=attempt,
                    http_status=status,
                )
                return response
            self._log.info(
                LogEvent.DEPENDENCY_RETRY,
                dependency=self._dependency,
                method=outbound.method,
                route=outbound.route,
                attempts=attempt,
                http_status=status,
            )
            self._sleep(RETRY_DELAYS_SECONDS[attempt - 1])
        return None

    def close(self) -> None:
        """Close the pooled connections."""
        self._client.close()
