"""Every answer of a shared service is bounded (SPEC_DECISIONS 102; review 3 F7).

Size: a declared `Content-Length` over `HTTP_MAX_RESPONSE_BYTES` is refused before the body
is read, and a body read as a stream is abandoned as soon as it passes the limit. Encoding:
the bank asks for `identity` and refuses any other `Content-Encoding` without decoding it.
Time: a body that trickles in is abandoned at `HTTP_TOTAL_TIMEOUT_SECONDS`. Each is the
permanent class MALFORMED_RESPONSE, in every one of the four clients.
"""

from __future__ import annotations

import gzip
import itertools
import tracemalloc
import uuid
from collections.abc import Callable, Iterator

import httpx
import pytest

from bank_testkit.memory.clocks import CountingIds
from bank_testkit.scripted_http import Quiet
from onepledge_bank_agent.domain.enums import FailureReason
from onepledge_bank_agent.domain.errors import PipelineStageError
from onepledge_bank_agent.domain.vin import Vin
from onepledge_bank_agent.infrastructure.http.base import CallPolicy, ResponseBounds, RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.key_holder_client import KeyHolderHttpClient
from onepledge_bank_agent.infrastructure.http.outside_records import OutsideRecordsClient
from onepledge_bank_agent.infrastructure.http.registry_client import RegistryHttpClient

pytestmark = pytest.mark.unit

LIMIT = 1_000
POINT = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA="
type Handler = Callable[[httpx.Request], httpx.Response]


class SteppingClock:
    """Each reading is one second after the last: one chunk per second."""

    def __init__(self) -> None:
        self._ticks = itertools.count()

    def __call__(self) -> float:
        return float(next(self._ticks))


def caller(dependency: str, handler: Handler, seen: list[httpx.Request]) -> RetryingHttpCaller:
    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(base_url="http://service.test", transport=httpx.MockTransport(record))
    bounds = ResponseBounds(max_bytes=LIMIT, total_seconds=30.0)
    return RetryingHttpCaller(
        client, "k" * 40, dependency, Quiet(), CallPolicy(lambda _: None, bounds, SteppingClock())
    )


def call_outside(handler: Handler, seen: list[httpx.Request]) -> object:
    return OutsideRecordsClient(caller("outside_records", handler, seen)).title(Vin("1HGCM82633A004352"))


def call_holder(handler: Handler, seen: list[httpx.Request]) -> object:
    client = KeyHolderHttpClient(caller("key_holder_1", handler, seen), "key_holder_1", CountingIds())
    return client.evaluate([POINT])


def call_certificate(handler: Handler, seen: list[httpx.Request]) -> object:
    return RegistryHttpClient(caller("registry", handler, seen)).certificate(uuid.uuid4())


def call_outbox(handler: Handler, seen: list[httpx.Request]) -> object:
    return RegistryHttpClient(caller("registry", handler, seen)).outbox(100)


CLIENTS = {
    "outside records": call_outside,
    "key holder": call_holder,
    "registry certificate": call_certificate,
    "registry outbox": call_outbox,
}


def padded(size: int) -> bytes:
    """A JSON object of exactly `size` bytes."""
    head = b'{"pad": "'
    return head + b"x" * (size - len(head) - 2) + b'"}'


def chunks(body: bytes, size: int = 1) -> Iterator[bytes]:
    for start in range(0, len(body), size):
        yield body[start : start + size]


ANSWERS: dict[str, Handler] = {
    "one byte over the limit, declared": lambda r: httpx.Response(200, content=padded(LIMIT + 1)),
    "one byte over the limit, not declared": lambda r: httpx.Response(
        200, content=chunks(padded(LIMIT + 1), 100)
    ),
    "a gzip answer": lambda r: httpx.Response(
        200, content=gzip.compress(b"{}"), headers={"Content-Encoding": "gzip"}
    ),
    "one byte per second": lambda r: httpx.Response(200, content=chunks(padded(200))),
}


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("answer", ANSWERS)
def test_an_unbounded_answer_is_malformed_in_every_client(client: str, answer: str) -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(PipelineStageError) as raised:
        CLIENTS[client](ANSWERS[answer], seen)
    assert (raised.value.reason, raised.value.transient) == (FailureReason.MALFORMED_RESPONSE, False)
    assert len(seen) == 1, "a permanent class: no retry"
    assert seen[0].headers["Accept-Encoding"] == "identity"


def test_a_body_at_the_limit_is_read() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(PipelineStageError) as raised:  # read in full, then not a certificate
        call_certificate(lambda r: httpx.Response(200, content=padded(LIMIT)), seen)
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE
    client = caller("registry", lambda r: httpx.Response(200, content=padded(LIMIT)), [])
    assert len(client.get("/x", "/x").content) == LIMIT


def test_a_huge_padding_is_abandoned_without_holding_it() -> None:
    """The reproduction of F7 at 400 MB: the read stops after the limit, memory stays small."""
    consumed: list[int] = []

    def endless() -> Iterator[bytes]:
        yield b'{"messages": [], "has_more": false, "pad": "'
        for _ in range(400 * 16):
            consumed.append(64 * 1024)
            yield b"x" * (64 * 1024)

    client = caller("registry", lambda r: httpx.Response(200, content=endless()), [])
    tracemalloc.start()
    try:
        with pytest.raises(PipelineStageError):
            RegistryHttpClient(client).outbox(100)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert sum(consumed) <= LIMIT + 64 * 1024
    assert peak < 50 * 1024 * 1024
