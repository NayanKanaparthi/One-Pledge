"""One classification of outbound HTTP failures for all four dependency clients (SPEC_DECISIONS 49).

Every row of the table is driven through every client over a scripted transport: outside
records, key holder 1, key holder 2 and the registry. Reproduced first (WP2 review 1, F4):
a 200 with `Content-Encoding: gzip` and a body that is not gzip escaped the client as
`httpx.DecodingError` and failed the tape with INTERNAL_ERROR.
"""

from __future__ import annotations

import ast
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from bank_testkit.memory.clocks import CountingIds
from onepledge_bank_agent.domain.enums import FailureReason
from onepledge_bank_agent.domain.errors import PipelineStageError
from onepledge_bank_agent.domain.vin import Vin
from onepledge_bank_agent.infrastructure.http.base import RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.key_holder_client import KeyHolderHttpClient
from onepledge_bank_agent.infrastructure.http.outside_records import OutsideRecordsClient
from onepledge_bank_agent.infrastructure.http.registry_client import RegistryHttpClient

pytestmark = pytest.mark.unit

type Handler = Callable[[httpx.Request], httpx.Response]
POINT = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA="
SOURCE = Path(__file__).resolve().parents[2] / "src" / "onepledge_bank_agent"


class Quiet:
    def info(self, event: str, **fields: object) -> None:
        """Nothing."""

    def warning(self, event: str, **fields: object) -> None:
        """Nothing."""

    def failure(self, event: str, error: BaseException, **fields: object) -> None:
        """Nothing."""


def caller(dependency: str, handler: Handler) -> RetryingHttpCaller:
    client = httpx.Client(base_url="http://service.test", transport=httpx.MockTransport(handler))
    return RetryingHttpCaller(client, "k" * 40, dependency, Quiet(), lambda _: None)


def call_outside(handler: Handler) -> object:
    return OutsideRecordsClient(caller("outside_records", handler)).title(Vin("1HGCM82633A004352"))


def call_holder(number: int) -> Callable[[Handler], object]:
    def call(handler: Handler) -> object:
        dependency = f"key_holder_{number}"
        return KeyHolderHttpClient(caller(dependency, handler), dependency, CountingIds()).evaluate([POINT])

    return call


def call_registry(handler: Handler) -> object:
    return RegistryHttpClient(caller("registry", handler)).certificate(uuid.uuid4())


CLIENTS = {
    "outside_records": call_outside,
    "key_holder_1": call_holder(1),
    "key_holder_2": call_holder(2),
    "registry": call_registry,
}


def raising(error: type[httpx.RequestError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("canary 1HGCM82633A004352", request=request)

    return handler


def status(code: int, **headers: str) -> Handler:
    return lambda request: httpx.Response(
        code, json={"error": {"code": "X", "message": "m"}}, headers=headers
    )


def gzip_junk(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"this is not gzip", headers={"Content-Encoding": "gzip"})


def invalid_url(request: httpx.Request) -> httpx.Response:
    raise httpx.InvalidURL("bad")


ROWS: list[tuple[str, Handler, bool, FailureReason, int | None]] = [
    ("connection refused", raising(httpx.ConnectError), True, FailureReason.CONNECTION_FAILED, None),
    ("reset", raising(httpx.ReadError), True, FailureReason.CONNECTION_FAILED, None),
    ("protocol", raising(httpx.RemoteProtocolError), True, FailureReason.CONNECTION_FAILED, None),
    ("connect timeout", raising(httpx.ConnectTimeout), True, FailureReason.TIMEOUT, None),
    ("read timeout", raising(httpx.ReadTimeout), True, FailureReason.TIMEOUT, None),
    ("pool timeout", raising(httpx.PoolTimeout), True, FailureReason.TIMEOUT, None),
    ("429", status(429), True, FailureReason.RATE_LIMITED, 429),
    ("500", status(500), True, FailureReason.SERVER_ERROR, 500),
    ("502", status(502), True, FailureReason.SERVER_ERROR, 502),
    ("503", status(503), True, FailureReason.SERVER_ERROR, 503),
    ("504", status(504), True, FailureReason.SERVER_ERROR, 504),
    ("401", status(401), False, FailureReason.UNAUTHENTICATED, 401),
    ("403", status(403), False, FailureReason.FORBIDDEN, 403),
    ("400", status(400), False, FailureReason.REQUEST_REFUSED, 400),
    ("418", status(418), False, FailureReason.REQUEST_REFUSED, 418),
    ("301", status(301, location="http://elsewhere.test/"), False, FailureReason.MALFORMED_RESPONSE, 301),
    ("gzip junk", gzip_junk, False, FailureReason.MALFORMED_RESPONSE, None),
    ("not json", lambda r: httpx.Response(200, text="<html>"), False, FailureReason.MALFORMED_RESPONSE, 200),
    (
        "wrong shape",
        lambda r: httpx.Response(200, json={"x": 1}),
        False,
        FailureReason.MALFORMED_RESPONSE,
        200,
    ),
    ("unknown library error", invalid_url, False, FailureReason.MALFORMED_RESPONSE, None),
]


@pytest.mark.parametrize("client", sorted(CLIENTS))
@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
def test_every_failure_of_every_client_is_classified_by_the_one_table(
    client: str, row: tuple[str, Handler, bool, FailureReason, int | None]
) -> None:
    name, handler, transient, reason, http_status = row
    with pytest.raises(PipelineStageError) as raised:
        CLIENTS[client](handler)
    error = raised.value
    assert (error.transient, error.reason, error.http_status, error.dependency) == (
        transient,
        reason,
        http_status,
        client,
    ), name
    assert "1HGCM82633A004352" not in repr(error.failure_facts())


def test_a_redirect_is_never_followed() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://service.test/v1/certificates/other"})

    with pytest.raises(PipelineStageError):
        call_registry(handler)
    assert len(seen) == 1


def calls(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            found.append((node.func.id, node.lineno))
    return found


def test_only_the_classification_builds_a_transient_failure_or_names_a_status_class() -> None:
    """The class is impossible to reintroduce: no client builds an Unavailable error or tests 429 or 5xx."""
    http = SOURCE / "infrastructure" / "http"
    allowed = {http / "classification.py"}
    violations = [
        f"{path.name}:{line} builds {name}"
        for path in sorted(http.glob("*.py"))
        if path not in allowed
        for name, line in calls(path)
        if name.endswith("UnavailableError")
    ]
    statuses = (401, 403, 429, 500, 502, 503, 504)
    literals = [
        f"{path.name}:{node.lineno} compares with {constant.value}"
        for path in sorted(http.glob("*.py"))
        if path not in allowed | {http / "base.py"}
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Compare)
        for constant in ast.walk(node)
        if isinstance(constant, ast.Constant) and constant.value in statuses
    ]
    assert violations == [] and literals == []
