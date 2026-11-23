"""Outside records: a canonical X-Request-ID only; a client hang-up is no failure (SPEC_DECISIONS 75)."""

from __future__ import annotations

import asyncio
import io
import json
import re
from collections.abc import Callable, Iterator, MutableMapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from onepledge_outside_records.infrastructure.logging_setup import configure_logging
from onepledge_outside_records.interface.http.plumbing import AccessLogMiddleware, install_error_handlers

CANONICAL = "1f0e5f7c-7f3a-4b8e-9d55-2c1f0a9b3e77"
SPELLINGS = ("{" + CANONICAL + "}", "urn:uuid:" + CANONICAL, CANONICAL.upper(), CANONICAL.replace("-", ""))
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.mark.integration
def test_an_x_request_id_in_another_spelling_is_replaced_not_rewritten(
    call: Callable[..., httpx.Response],
) -> None:
    assert call("GET", "/healthz", headers={"X-Request-ID": CANONICAL}).headers["x-request-id"] == CANONICAL
    for spelling in SPELLINGS:
        echoed = call("GET", "/healthz", headers={"X-Request-ID": spelling}).headers["x-request-id"]
        assert UUID.fullmatch(echoed) and echoed != CANONICAL


def body_reading_app() -> FastAPI:
    """The service's plumbing around a route that reads its body, as any body-reading route would."""
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(AccessLogMiddleware)

    @app.post("/v1/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    return app


@pytest.mark.unit
def test_a_client_that_hangs_up_mid_body_is_one_info_record_and_no_failure() -> None:
    stream = io.StringIO()
    configure_logging("outside_records", "INFO", stream)
    messages: Iterator[dict[str, Any]] = iter(
        [{"type": "http.request", "body": b"x" * 20, "more_body": True}, {"type": "http.disconnect"}]
    )
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return next(messages)

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(dict(message))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/echo",
        "raw_path": b"/v1/echo",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-length", b"500")],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 2),
    }
    asyncio.run(body_reading_app()(scope, receive, send))
    records = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    assert [(r["level"], r["event"]) for r in records] == [("info", "client_disconnected")]
    assert not [m for m in sent if m["type"] == "http.response.start"]


# SPEC_DECISIONS 112.8: a repeated credential is 401; a repeated Idempotency-Key, Content-Type,
# Content-Length or X-Request-ID is 422 DUPLICATE.
def problem(response: httpx.Response) -> tuple[int, str, list[tuple[str, str]]]:
    error = response.json()["error"]
    return response.status_code, error["code"], [(d["field"], d["issue"]) for d in error.get("details", [])]


@pytest.mark.integration
def test_a_repeated_api_key_is_unauthenticated_even_when_both_are_valid(
    call: Callable[..., httpx.Response], api_key: str, example_records: Path
) -> None:
    vin = (example_records / "titles.csv").read_text().splitlines()[1].split(",")[0]
    response = call("GET", f"/v1/titles/{vin}", headers=[("X-API-Key", api_key), ("X-API-Key", api_key)])
    assert problem(response) == (401, "UNAUTHENTICATED", [])


@pytest.mark.integration
@pytest.mark.parametrize("name", ["Idempotency-Key", "Content-Type", "Content-Length"])
def test_another_repeated_header_is_duplicate_after_authentication(
    call: Callable[..., httpx.Response], api_key: str, example_records: Path, name: str
) -> None:
    vin = (example_records / "titles.csv").read_text().splitlines()[1].split(",")[0]
    response = call("GET", f"/v1/titles/{vin}", headers=[("X-API-Key", api_key), (name, "0"), (name, "0")])
    assert problem(response) == (422, "VALIDATION_ERROR", [(name, "DUPLICATE")])
    assert call("GET", f"/v1/titles/{vin}", headers=[(name, "0"), (name, "0")]).status_code == 401


@pytest.mark.integration
def test_a_repeated_request_id_is_duplicate(call: Callable[..., httpx.Response]) -> None:
    response = call("GET", "/healthz", headers=[("X-Request-ID", CANONICAL), ("X-Request-ID", CANONICAL)])
    assert problem(response) == (422, "VALIDATION_ERROR", [("X-Request-ID", "DUPLICATE")])
