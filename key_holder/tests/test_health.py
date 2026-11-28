"""`GET /healthz` in process: the contract's `Health` schema, no redirect, no documentation routes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from onepledge_key_holder.application.report_health import ReportHealth
from onepledge_key_holder.domain.identity import HolderId, KeyVersion
from onepledge_key_holder.interface.http import create_app

pytestmark = [pytest.mark.unit, pytest.mark.contract]

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACT = REPOSITORY / "contracts" / "key_holder.yaml"


class InProcessApi:
    """Calls the ASGI app in-process through httpx (no network, no server)."""

    def __init__(self, app: FastAPI) -> None:
        self._runner = asyncio.Runner()
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        self._client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    def request(self, method: str, url: str) -> httpx.Response:
        """One request; the query string goes in `url`."""
        return self._runner.run(self._client.request(method, url))

    def close(self) -> None:
        """Close the client and the event loop."""
        self._runner.run(self._client.aclose())
        self._runner.close()


@pytest.fixture
def call_app() -> Iterator[Callable[[FastAPI], InProcessApi]]:
    """Builds in-process clients for apps made inside a test, and closes them afterwards."""
    made: list[InProcessApi] = []

    def build(app: FastAPI) -> InProcessApi:
        made.append(InProcessApi(app))
        return made[-1]

    yield build
    for api in made:
        api.close()


def health_schema() -> dict[str, Any]:
    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    schema: dict[str, Any] = document["components"]["schemas"]["Health"]
    return schema


def app_for(holder: HolderId, version: int) -> FastAPI:
    return create_app(ReportHealth(holder, KeyVersion(version)))


@pytest.mark.parametrize(("holder", "version"), [(HolderId.KH1, 1), (HolderId.KH2, 7)])
def test_health_is_the_contract_schema_with_the_configured_identity(
    call_app: Callable[[FastAPI], InProcessApi], holder: HolderId, version: int
) -> None:
    response = call_app(app_for(holder, version)).request("GET", "/healthz")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    Draft202012Validator(health_schema()).validate(body)
    assert body == {
        "status": "ok",
        "service": "key_holder",
        "holder_id": holder.value,
        "active_key_version": version,
    }


def test_health_has_no_test_double_flag(call_app: Callable[[FastAPI], InProcessApi]) -> None:
    body = call_app(app_for(HolderId.KH1, 1)).request("GET", "/healthz").json()
    assert "test_double" not in body


def test_every_field_of_the_body_is_a_property_of_the_schema(
    call_app: Callable[[FastAPI], InProcessApi],
) -> None:
    body = call_app(app_for(HolderId.KH2, 3)).request("GET", "/healthz").json()
    schema = health_schema()
    assert set(body) <= set(schema["properties"])
    assert set(schema["required"]) <= set(body)


def test_health_ignores_a_query_string(call_app: Callable[[FastAPI], InProcessApi]) -> None:
    response = call_app(app_for(HolderId.KH1, 1)).request("GET", "/healthz?cache=1")
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/healthz/", "/docs", "/redoc", "/openapi.json", "/v1/evaluate/"])
def test_a_trailing_slash_or_documentation_path_is_not_found_never_a_redirect(
    call_app: Callable[[FastAPI], InProcessApi], path: str
) -> None:
    response = call_app(app_for(HolderId.KH1, 1)).request("GET", path)
    assert response.status_code == 404
    assert "location" not in response.headers
