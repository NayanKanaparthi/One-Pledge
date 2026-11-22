"""The HTTP surface of outside records (SPEC_DECISIONS 34, 35, 36, 41, 43.5).

No redirect, the request id on the one error record of every 500, health that ignores its
query, and `Allow` on every 405. Every declared route is walked, so a route added later is
covered without editing this file.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

import httpx
import psycopg
import pytest
import structlog
from fastapi import FastAPI
from fastapi.routing import APIRoute
from psycopg import sql
from sqlalchemy import Engine

from onepledge_outside_records.domain.records import (
    ApiClient,
    Contract,
    PaymentPage,
    PaymentQuery,
    TitleRecord,
    VinText,
)
from onepledge_outside_records.infrastructure.db.engines import make_engine
from onepledge_outside_records.infrastructure.db.repositories import SqlRecordsReader
from onepledge_outside_records.infrastructure.logging_setup import configure_logging
from onepledge_outside_records.interface.http.app import create_app

if TYPE_CHECKING:
    from conftest import Database, InProcessApi

pytestmark = [pytest.mark.integration, pytest.mark.contract]

CANARY = "ZQXJ7CANARY9"
CANARY_VIN = "ZQXJ7CANARY9AAAAA"


def routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, path template) the app declares."""
    return sorted(
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    )


def concrete(template: str) -> str:
    return "/".join(CANARY_VIN if part.startswith("{") else part for part in template.split("/"))


def assert_no_canary(response: httpx.Response) -> None:
    assert CANARY not in f"{response.status_code} {response.reason_phrase}"
    assert all(CANARY not in name and CANARY not in value for name, value in response.headers.items())
    assert CANARY not in response.text


@pytest.fixture
def surface(app_engine: Engine, call_app: Callable[[FastAPI], InProcessApi]) -> tuple[FastAPI, InProcessApi]:
    app = create_app(SqlRecordsReader(app_engine))
    return app, call_app(app)


def test_every_route_with_a_trailing_slash_is_a_plain_404(
    surface: tuple[FastAPI, InProcessApi], api_key: str
) -> None:
    app, api = surface
    declared = routes(app)
    assert len(declared) == 4
    for method, template in declared:
        url = f"{concrete(template)}/?limit={CANARY}&{CANARY}=1"
        response = api.request(method, url, headers={"X-API-Key": api_key})
        assert response.status_code == 404, template
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert "location" not in response.headers
        assert_no_canary(response)


def test_the_reviewer_trailing_slash_request_is_404(surface: tuple[FastAPI, InProcessApi]) -> None:
    _, api = surface
    response = api.request("GET", f"/v1/titles/{CANARY_VIN}/?limit={CANARY}")
    assert response.status_code == 404 and "location" not in response.headers
    assert_no_canary(response)


def test_health_ignores_query_parameters(surface: tuple[FastAPI, InProcessApi]) -> None:
    _, api = surface
    for query in (f"?{CANARY}=1", "?_=1700000000", "?limit=x&limit=y"):
        response = api.request("GET", f"/healthz{query}")
        assert (response.status_code, response.json()) == (
            200,
            {"status": "ok", "service": "outside_records"},
        )


def test_every_405_carries_allow(surface: tuple[FastAPI, InProcessApi]) -> None:
    app, api = surface
    by_path: dict[str, set[str]] = {}
    for method, template in routes(app):
        by_path.setdefault(template, set()).add(method)
    for template, methods in by_path.items():
        for wrong in {"POST", "PUT", "DELETE", "PATCH"} - methods:
            response = api.request(wrong, concrete(template))
            assert response.status_code == 405, (wrong, template)
            assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
            assert {part.strip() for part in response.headers["allow"].split(",")} == methods


class FailingReader:
    """A records reader whose lookups fail the way a defect would; its client always authenticates."""

    def title(self, vin: VinText) -> TitleRecord | None:
        raise RuntimeError(CANARY)

    def payments(self, query: PaymentQuery) -> PaymentPage:
        raise RuntimeError(CANARY)

    def contracts(self, vin: VinText) -> tuple[Contract, ...]:
        raise RuntimeError(CANARY)

    def active_client(self, api_key_sha256: str) -> ApiClient | None:
        return ApiClient(client_id=str(uuid.uuid4()), name="harbor")


@pytest.fixture
def log() -> io.StringIO:
    stream = io.StringIO()
    configure_logging("outside_records", "INFO", stream)
    return stream


def records(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_a_500_writes_one_error_record_with_the_request_id(
    log: io.StringIO, call_app: Callable[[FastAPI], InProcessApi]
) -> None:
    api = call_app(create_app(FailingReader()))
    request_id = str(uuid.uuid4())
    headers = {"X-API-Key": "opk_x", "X-Request-ID": request_id}
    response = api.request("GET", "/v1/titles/1HGCM82633A004352", headers=headers)
    after = api.request("GET", "/healthz")
    assert response.status_code == 500
    assert response.json()["error"]["request_id"] == request_id == response.headers["x-request-id"]
    errors = [record for record in records(log) if record["level"] == "error"]
    assert len(errors) == 1 and errors[0]["request_id"] == request_id
    assert errors[0]["exception_type"] == "RuntimeError"
    later = [r for r in records(log) if r.get("request_id") == after.headers["x-request-id"]]
    assert [r["event"] for r in later] == ["http_request"]
    assert CANARY not in log.getvalue()
    assert structlog.contextvars.get_contextvars() == {}


@pytest.fixture
def revoked_contracts(database: Database) -> Iterator[None]:
    """The reviewer's reproduction: the app role loses SELECT on contracts, then gets it back."""
    with psycopg.connect(database.admin_url, autocommit=True, dbname=database.target.database) as admin:
        admin.execute(sql.SQL("REVOKE SELECT ON contracts FROM {}").format(sql.Identifier(database.app_role)))
    yield
    with psycopg.connect(database.admin_url, autocommit=True, dbname=database.target.database) as admin:
        admin.execute(sql.SQL("GRANT SELECT ON contracts TO {}").format(sql.Identifier(database.app_role)))


def test_a_database_permission_failure_is_one_error_record_with_the_request_id(
    database: Database,
    api_key: str,
    revoked_contracts: None,
    log: io.StringIO,
    call_app: Callable[[FastAPI], InProcessApi],
) -> None:
    engine = make_engine(database.app_url)
    api = call_app(create_app(SqlRecordsReader(engine)))
    request_id = "44444444-4444-4444-8444-444444444444"
    response = api.request(
        "GET", "/v1/contracts/1HGCM82633A004352", headers={"X-API-Key": api_key, "X-Request-ID": request_id}
    )
    engine.dispose()
    assert response.status_code == 500
    errors = [record for record in records(log) if record["level"] == "error"]
    assert [record["request_id"] for record in errors] == [request_id]


# SPEC_DECISIONS 66: every record of a 5xx response carries error_code, http_status, request_id
# and exception_type; one `error` record for a 500, none for any other status.
FAILURE_KEYS = ("error_code", "http_status", "request_id", "exception_type")


def assert_failure_records(stream: io.StringIO, response: httpx.Response, exception_type: str) -> None:
    request_id = response.headers["x-request-id"]
    mine = [r for r in records(stream) if r.get("request_id") == request_id]
    assert len(mine) == 2, mine
    for record in mine:
        assert all(record.get(key) is not None for key in FAILURE_KEYS), record
        assert record["error_code"] == response.json()["error"]["code"]
        assert (record["http_status"], record["exception_type"]) == (response.status_code, exception_type)
    assert len([r for r in mine if r["level"] == "error"]) == (1 if response.status_code == 500 else 0)


def test_every_record_of_an_unexpected_500_carries_the_failure_keys(
    log: io.StringIO, call_app: Callable[[FastAPI], InProcessApi]
) -> None:
    api = call_app(create_app(FailingReader()))
    response = api.request("GET", "/v1/titles/1HGCM82633A004352", headers={"X-API-Key": "opk_x"})
    assert response.status_code == 500
    assert_failure_records(log, response, "RuntimeError")


def test_every_record_of_a_database_outage_carries_the_failure_keys(
    log: io.StringIO, call_app: Callable[[FastAPI], InProcessApi]
) -> None:
    engine = make_engine("postgresql://nobody:pw@127.0.0.1:1/none")
    api = call_app(create_app(SqlRecordsReader(engine)))
    response = api.request("GET", "/v1/titles/1HGCM82633A004352", headers={"X-API-Key": "opk_x"})
    engine.dispose()
    assert response.status_code == 503
    assert_failure_records(log, response, "ServiceUnavailableError")


def test_every_record_of_a_permission_failure_carries_the_failure_keys(
    database: Database,
    api_key: str,
    revoked_contracts: None,
    log: io.StringIO,
    call_app: Callable[[FastAPI], InProcessApi],
) -> None:
    engine = make_engine(database.app_url)
    api = call_app(create_app(SqlRecordsReader(engine)))
    response = api.request("GET", "/v1/contracts/1HGCM82633A004352", headers={"X-API-Key": api_key})
    engine.dispose()
    assert response.status_code == 500
    assert_failure_records(log, response, "ProgrammingError")
