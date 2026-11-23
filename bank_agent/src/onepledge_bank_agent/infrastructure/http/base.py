"""The shared outbound HTTP client: timeouts, no redirects, pooling, bounded retries (SPEC 16.6).

Each call is retried up to two more times (after 0.2 s and 0.4 s) on a transport error,
a timeout, 502, 503 or 504. Every failure is named by the one classification of
`classification.py` (SPEC_DECISIONS 49): nothing the HTTP library raises reaches a stage
as an unexpected error, and redirects are never followed. Every call the bank agent makes
is idempotent by design: a key holder evaluation is a pure function, a registry submission carries its
idempotency key, an acknowledgement and a repeated case position change nothing.
Only route templates are logged, never a path or a query string, because outside
records carry the VIN in both.

Every answer is bounded (SPEC_DECISIONS 102): the bank asks for `identity` encoding and
refuses any other without decoding it; a declared `Content-Length` over the byte limit is
refused before the body is read; the body is read as a stream and abandoned as soon as it
passes the limit or the call's total deadline. Each is the permanent class
MALFORMED_RESPONSE, never retried.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import OperationalLog
from onepledge_bank_agent.domain.text_rules import is_digits
from onepledge_bank_agent.infrastructure.http.classification import (
    ERRORS,
    MALFORMED,
    Classified,
    classify_exception,
    classify_status,
)
from onepledge_bank_agent.infrastructure.request_context import current_request_id

RETRY_DELAYS_SECONDS = (0.2, 0.4)
RETRY_STATUSES = frozenset({502, 503, 504})
LIBRARY_ERRORS = (httpx.HTTPError, httpx.StreamError, httpx.InvalidURL)


type JsonBody = Mapping[str, object]


@dataclass(frozen=True)
class OutboundRequest:
    """One call: method, route template (the only form ever logged), path, query or JSON body, and an
    optional hook run before every attempt (the registry client counts each send with it)."""

    method: str
    route: str
    path: str
    params: Mapping[str, str | int] | None = None
    body: JsonBody | None = None
    headers: Mapping[str, str] | None = None
    before_each: Callable[[], None] | None = None


@dataclass(frozen=True)
class HttpTimeouts:
    """Connect and read timeouts of one dependency."""

    connect_seconds: float
    read_seconds: float


@dataclass(frozen=True)
class ResponseBounds:
    """The most bytes an answer may hold and the most seconds one call may take
    (`HTTP_MAX_RESPONSE_BYTES`, `HTTP_TOTAL_TIMEOUT_SECONDS`)."""

    max_bytes: int = 4_194_304
    total_seconds: float = 30.0


@dataclass(frozen=True)
class CallPolicy:
    """How one caller waits between retries, bounds each answer and reads the time."""

    sleep: Callable[[float], None]
    bounds: ResponseBounds = ResponseBounds()
    clock: Callable[[], float] = time.monotonic


class _UnboundedAnswerError(Exception):
    """The answer broke a bound of SPEC_DECISIONS 102; carries nothing of the body."""


IDENTITY = "identity"


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
        policy: CallPolicy,
    ) -> None:
        self._bounds = policy.bounds
        self._clock = policy.clock
        self._client = client
        self._api_key = api_key
        self._dependency = dependency
        self.errors = ERRORS[dependency]
        self._log = log
        self._sleep = policy.sleep

    def get(self, route: str, path: str, params: Mapping[str, str | int] | None = None) -> httpx.Response:
        """GET `path`, logged as `route`; the answer, or the dependency's named error (see `send`)."""
        return self.send(OutboundRequest("GET", route, path, params=params))

    def post(
        self, route: str, path: str, body: JsonBody | None = None, headers: Mapping[str, str] | None = None
    ) -> httpx.Response:
        """POST a JSON body to `path`, logged as `route`; as `get`."""
        return self.send(OutboundRequest("POST", route, path, body=body, headers=headers))

    def send(self, outbound: OutboundRequest) -> httpx.Response:
        """Send with the retry rule. Returns the answer when it is not a retryable failure; raises the
        dependency's named error (SPEC_DECISIONS 49) when retries ran out or the library failed."""
        headers = {
            "X-API-Key": self._api_key,
            "Accept": "application/json",
            "Accept-Encoding": IDENTITY,
            **dict(outbound.headers or {}),
        }
        request_id = current_request_id()
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        attempts, attempt = 1 + len(RETRY_DELAYS_SECONDS), 0
        while True:
            attempt += 1
            if outbound.before_each is not None:
                outbound.before_each()
            status: int | None = None
            try:
                response = self._bounded(outbound, headers)
            except _UnboundedAnswerError:
                self._log_unavailable(outbound, attempt, None, MALFORMED)
                raise self.errors.of(None, MALFORMED) from None
            except LIBRARY_ERRORS as error:
                classified = classify_exception(error)
                if not classified.transient:
                    self._log_unavailable(outbound, attempt, status, classified)
                    raise self.errors.of(None, classified) from None
            else:
                if response.status_code not in RETRY_STATUSES:
                    return response
                status = response.status_code
                classified = classify_status(status)
            if attempt == attempts:
                self._log_unavailable(outbound, attempt, status, classified)
                raise self.errors.of(status, classified)
            self._log.info(
                LogEvent.DEPENDENCY_RETRY,
                dependency=self._dependency,
                method=outbound.method,
                route=outbound.route,
                attempts=attempt,
                http_status=status,
                reason=classified.reason.value,
            )
            self._sleep(RETRY_DELAYS_SECONDS[attempt - 1])

    def _bounded(self, outbound: OutboundRequest, headers: Mapping[str, str]) -> httpx.Response:
        """One attempt, its answer read within the bounds; a retryable status is not read at all."""
        deadline = self._clock() + self._bounds.total_seconds
        request = self._client.build_request(
            outbound.method, outbound.path, params=outbound.params, json=outbound.body, headers=headers
        )
        streamed = self._client.send(request, stream=True)
        try:
            body = b"" if streamed.status_code in RETRY_STATUSES else self._body(streamed, deadline)
        finally:
            streamed.close()
        return httpx.Response(streamed.status_code, headers=streamed.headers, content=body, request=request)

    def _body(self, streamed: httpx.Response, deadline: float) -> bytes:
        encoding = streamed.headers.get("Content-Encoding", IDENTITY).strip().lower()
        declared = streamed.headers.get("Content-Length", "").strip()
        limit = self._bounds.max_bytes
        if encoding not in ("", IDENTITY) or (is_digits(declared) and int(declared) > limit):
            raise _UnboundedAnswerError
        parts: list[bytes] = []
        received = 0
        for chunk in streamed.iter_bytes():  # identity only, so the bytes are the raw bytes
            received += len(chunk)
            if received > limit or self._clock() > deadline:
                raise _UnboundedAnswerError
            parts.append(chunk)
        return b"".join(parts)

    def _log_unavailable(
        self, outbound: OutboundRequest, attempt: int, status: int | None, classified: Classified
    ) -> None:
        self._log.warning(
            LogEvent.DEPENDENCY_UNAVAILABLE,
            dependency=self._dependency,
            method=outbound.method,
            route=outbound.route,
            attempts=attempt,
            http_status=status,
            reason=classified.reason.value,
        )

    def close(self) -> None:
        """Close the pooled connections."""
        self._client.close()
