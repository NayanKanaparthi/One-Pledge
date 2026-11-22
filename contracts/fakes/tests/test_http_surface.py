"""The HTTP surface of both doubles (SPEC_DECISIONS 35, 36, 38, 41, 43.5).

No redirect, the request id on every record of a request, bounded JSON bodies, health
probes that ignore their query, `/_test/` routes that refuse unknown parameters, and an
`Allow` header on every 405. Every route the apps declare is walked, so a route added
later is covered without editing this file.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI
from fastapi.routing import APIRoute

from onepledge_contract_fakes.errors import DomainError
from onepledge_contract_fakes.key_holder.app import create_key_holder_app
from onepledge_contract_fakes.logging_setup import configure_logging
from onepledge_contract_fakes.registry.app import create_registry_app
from onepledge_contract_fakes.testing import (
    AsgiTestClient,
    FixedClock,
    key_holder_config,
    new_api_key,
    new_members,
    registry_config,
)
from onepledge_contract_fakes.web import (
    AsgiApp,
    Message,
    Receive,
    RequestContextMiddleware,
    Scope,
    Send,
    install_error_handlers,
)

pytestmark = pytest.mark.contract

CANARY = "ZQXJ7CANARY9"
CONTROL_KEY = "k" * 40
CONTROL = {"X-Test-Control-Key": CONTROL_KEY}
JSON_TYPE = {"content-type": "application/json"}


class Double:
    """One double app with its client, a member key and the log lines it wrote."""

    def __init__(self, name: str, app: FastAPI, api_key: str, log: io.StringIO) -> None:
        self.name = name
        self.app = app
        self.api_key = api_key
        self.client = AsgiTestClient(app)
        self._log = log

    def records(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self._log.getvalue().splitlines()]


def _registry(log: io.StringIO) -> Double:
    members, keys = new_members(1)
    config = registry_config(members, new_api_key(), test_control_key=CONTROL_KEY)
    return Double("registry", create_registry_app(config, FixedClock()), keys[0], log)


def _key_holder(log: io.StringIO) -> Double:
    members, keys = new_members(1)
    config = key_holder_config("kh1", members, test_control_key=CONTROL_KEY)
    return Double("key_holder", create_key_holder_app(config, FixedClock()), keys[0], log)


@pytest.fixture(params=["registry", "key_holder"])
def double(request: pytest.FixtureRequest) -> Iterator[Double]:
    log = io.StringIO()
    configure_logging(str(request.param), "INFO", log)
    built = _registry(log) if request.param == "registry" else _key_holder(log)
    yield built
    built.client.close()


def routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, path template) the app declares."""
    return sorted(
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    )


def concrete(template: str) -> str:
    """The template with the canary in every path parameter."""
    parts = [CANARY if part.startswith("{") else part for part in template.split("/")]
    return "/".join(parts)


def assert_no_canary(response: httpx.Response) -> None:
    assert CANARY not in f"{response.status_code} {response.reason_phrase}"
    assert all(CANARY not in name and CANARY not in value for name, value in response.headers.items())
    assert CANARY not in response.text


# SPEC_DECISIONS 35: no redirect, a trailing slash is an unknown route.


def test_every_route_with_a_trailing_slash_is_a_plain_404(double: Double) -> None:
    declared = routes(double.app)
    assert len(declared) >= 4
    for method, template in declared:
        url = f"{concrete(template)}/?{CANARY}={CANARY}"
        headers = {"X-API-Key": double.api_key, **CONTROL, **JSON_TYPE}
        response = double.client.send(method, url, content=b"{}", headers=headers)
        assert response.status_code == 404, (method, template)
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert "location" not in response.headers
        assert_no_canary(response)


def test_no_response_of_either_double_is_a_redirect(double: Double) -> None:
    for path in ("/", "//", "/v1", "/v1/", "/healthz/", f"/{CANARY}/", "/_test/stats/"):
        response = double.client.get(path, headers=CONTROL)
        assert not 300 <= response.status_code < 400, path
        assert "location" not in response.headers


# SPEC_DECISIONS 36 and 34 (F8): one error record per 500, carrying the response's request id.


def failing_app() -> tuple[FastAPI, list[dict[str, Any]]]:
    """An app with the doubles' plumbing whose routes fail, and the log context seen after each request."""
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    seen: list[dict[str, Any]] = []

    class ContextProbe:
        def __init__(self, inner: AsgiApp) -> None:
            self._inner = inner

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await self._inner(scope, receive, send)
            seen.append(dict(structlog.contextvars.get_contextvars()))

    app.add_middleware(ContextProbe)

    @app.get("/v1/unexpected")
    async def unexpected() -> None:
        raise RuntimeError(CANARY)

    @app.get("/v1/named")
    async def named() -> None:
        raise DomainError

    @app.get("/v1/fine")
    async def fine() -> dict[str, str]:
        return {"status": "ok"}

    return app, seen


@pytest.mark.parametrize("path", ["/v1/unexpected", "/v1/named"])
def test_a_500_writes_one_error_record_with_the_request_id(path: str) -> None:
    log = io.StringIO()
    configure_logging("registry", "INFO", log)
    app, seen = failing_app()
    request_id = str(uuid.uuid4())
    with AsgiTestClient(app) as client:
        response = client.get(path, headers={"X-Request-ID": request_id})
        after = client.get("/v1/fine")
    assert response.status_code == 500
    assert response.json()["error"]["request_id"] == request_id == response.headers["x-request-id"]
    records = [json.loads(line) for line in log.getvalue().splitlines()]
    errors = [record for record in records if record["level"] == "error"]
    assert len(errors) == 1
    assert errors[0]["request_id"] == request_id
    assert CANARY not in log.getvalue()
    later = [r for r in records if r.get("request_id") == after.headers["x-request-id"]]
    assert [r["event"] for r in later] == ["http_request"]
    assert all("request_id" not in context for context in seen)


def test_an_injected_500_writes_one_error_record_with_the_request_id(double: Double) -> None:
    armed = double.client.post(
        "/_test/fail-next", json={"count": 1, "status": 500, "when": "BEFORE_PROCESSING"}, headers=CONTROL
    )
    assert armed.status_code == 200
    target = "/v1/evaluate" if double.name == "key_holder" else "/v1/submissions"
    request_id = str(uuid.uuid4())
    response = double.client.post(target, json={}, headers={"X-Request-ID": request_id})
    assert response.status_code == 500
    errors = [record for record in double.records() if record["level"] == "error"]
    assert [record["request_id"] for record in errors] == [request_id]


async def call_asgi(app: AsgiApp, headers: list[tuple[bytes, bytes]]) -> tuple[int, dict[bytes, bytes]]:
    """One GET /healthz sent straight to the ASGI app, so any header bytes can be sent."""
    scope: Scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": "/healthz", "raw_path": b"/healthz", "query_string": b"",
        "root_path": "", "headers": headers, "server": ("testserver", 80), "client": ("127.0.0.1", 1),
    }  # fmt: skip
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = messages[0]
    return start["status"], dict(start["headers"])


def test_a_malformed_content_length_is_a_client_error_inside_the_request(double: Double) -> None:
    for value in ("\u00b2", "\u0663", "1.0", "-1", "+5", "9" * 40, "", " 1"):
        request_id = str(uuid.uuid4())
        headers = [(b"content-length", value.encode()), (b"x-request-id", request_id.encode())]
        status, sent = asyncio.run(call_asgi(double.app, headers))
        assert status == 422, value
        assert sent[b"x-request-id"] == request_id.encode()
        assert structlog.contextvars.get_contextvars() == {}
    assert not [record for record in double.records() if record["level"] == "error"]
    status, _ = asyncio.run(call_asgi(double.app, [(b"content-length", b"2000000")]))
    assert status == 413


# SPEC_DECISIONS 38: bounded JSON bodies on every route that reads one, `/_test/` included.


def body_routes(double: Double) -> list[tuple[str, dict[str, str]]]:
    """Every route that reads a JSON body, with the headers that get the request to the parser."""
    member = {"X-API-Key": double.api_key, "Idempotency-Key": hashlib.sha256(b"k").hexdigest()}
    if double.name == "key_holder":
        return [("/v1/evaluate", member), ("/_test/fail-next", CONTROL)]
    case = f"/v1/cases/{uuid.uuid4()}/resolution"
    return [
        ("/v1/submissions", member),
        (case, member),
        ("/_test/fail-next", CONTROL),
        ("/_test/tamper", CONTROL),
    ]


MALFORMED_BODIES = [
    b"",
    b"{",
    b'{"a": 1}{',
    b"\xff\xfe{}",
    b"[" * 200_000,
    b'{"a":' * 40 + b"1" + b"}" * 40,
    b'{"count": ' + b"9" * 5000 + b"}",
    b'{"seq": ' + b"1" * 31 + b"}",
    b'{"count": 1, "count": 1}',
    b'{"a": {"b": 1, "b": 2}}',
    b'{"count": NaN}',
    b'{"count": Infinity}',
    b'{"count": -Infinity}',
    b"[1, 2]",
    b'"text"',
    b"null",
    b"17",
]


@pytest.mark.parametrize("body", MALFORMED_BODIES, ids=range(len(MALFORMED_BODIES)))
def test_a_malformed_body_is_invalid_json_on_every_route(double: Double, body: bytes) -> None:
    for path, headers in body_routes(double):
        response = double.client.post_raw(path, body, headers={**headers, **JSON_TYPE})
        assert response.status_code == 422, (path, response.text)
        assert response.json()["error"]["details"] == [{"field": "body", "issue": "INVALID_JSON"}], path


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded", "json"])
def test_a_body_without_the_json_content_type_is_refused(double: Double, content_type: str | None) -> None:
    for path, headers in body_routes(double):
        sent = {**headers, **({"content-type": content_type} if content_type else {})}
        response = double.client.post_raw(path, b"{}", headers=sent)
        assert response.status_code == 422, path
        assert response.json()["error"]["details"] == [{"field": "Content-Type", "issue": "INVALID_VALUE"}]


def test_a_json_content_type_with_parameters_is_accepted(double: Double) -> None:
    headers = {**CONTROL, "content-type": "Application/JSON; charset=utf-8"}
    body = json.dumps({"count": 1, "status": 503, "when": "BEFORE_PROCESSING"}).encode()
    assert double.client.post_raw("/_test/fail-next", body, headers=headers).status_code == 200


@pytest.mark.parametrize("field", ["count", "status", "when"])
def test_a_null_test_control_value_is_invalid_type(double: Double, field: str) -> None:
    body = {"count": 1, "status": 503, "when": "BEFORE_PROCESSING", field: None}
    response = double.client.post("/_test/fail-next", json=body, headers=CONTROL)
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [{"field": field, "issue": "INVALID_TYPE"}]


def test_an_absent_test_control_value_is_required(double: Double) -> None:
    response = double.client.post("/_test/fail-next", json={"status": 503}, headers=CONTROL)
    assert response.status_code == 422
    assert {"field": "when", "issue": "REQUIRED"} in response.json()["error"]["details"]


@pytest.mark.parametrize(("value", "issue"), [(10**25, "OUT_OF_RANGE"), (None, "INVALID_TYPE")])
def test_tamper_seq_is_bounded(value: int | None, issue: str) -> None:
    log = io.StringIO()
    configure_logging("registry", "INFO", log)
    registry = _registry(log)
    response = registry.client.post(
        "/_test/tamper", json={"mode": "REWRITE_HISTORY", "seq": value}, headers=CONTROL
    )
    registry.client.close()
    assert response.status_code == 422
    assert {"field": "seq", "issue": issue} in response.json()["error"]["details"]


# SPEC_DECISIONS 34 (F12): an absent header is REQUIRED, a malformed one PATTERN_MISMATCH.


def test_idempotency_key_absent_is_required_and_malformed_is_pattern_mismatch() -> None:
    registry = _registry(io.StringIO())
    auth = {"X-API-Key": registry.api_key}
    absent = registry.client.post("/v1/submissions", json={}, headers=auth)
    malformed = registry.client.post("/v1/submissions", json={}, headers={**auth, "Idempotency-Key": "x"})
    registry.client.close()
    assert absent.json()["error"]["details"] == [{"field": "Idempotency-Key", "issue": "REQUIRED"}]
    assert malformed.json()["error"]["details"] == [{"field": "Idempotency-Key", "issue": "PATTERN_MISMATCH"}]


# SPEC_DECISIONS 41: health ignores its query; every `/_test/` route refuses unknown parameters.


def test_health_ignores_query_parameters(double: Double) -> None:
    for query in (f"?{CANARY}=1", "?_=1700000000", "?limit=x&limit=y"):
        response = double.client.get(f"/healthz{query}")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_every_test_control_route_refuses_unknown_parameters(double: Double) -> None:
    controls = [(m, p) for m, p in routes(double.app) if p.startswith("/_test/")]
    assert len(controls) >= 3
    for method, path in controls:
        headers = {**CONTROL, **JSON_TYPE}
        response = double.client.send(method, f"{path}?{CANARY}=1", content=b"{}", headers=headers)
        assert response.status_code == 422, (method, path)
        assert response.json()["error"]["details"] == [{"field": "query", "issue": "UNKNOWN_PARAMETER"}]
        assert_no_canary(response)


# SPEC_DECISIONS 43.5: every 405 names the route's methods in `Allow`.


def test_every_405_carries_allow(double: Double) -> None:
    by_path: dict[str, set[str]] = {}
    for method, template in routes(double.app):
        by_path.setdefault(template, set()).add(method)
    for template, methods in by_path.items():
        for wrong in {"GET", "POST", "PUT", "DELETE", "PATCH"} - methods:
            response = double.client.send(wrong, concrete(template), headers=CONTROL)
            assert response.status_code == 405, (wrong, template)
            assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
            allowed = {part.strip() for part in response.headers["allow"].split(",")}
            assert allowed == methods | ({"HEAD"} if "GET" in methods else set()) or allowed == methods
