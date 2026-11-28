"""`GET /healthz` in process: the contract's `Health` schema, no redirect, no documentation routes."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator
from onepledge_registry.interface.http import create_app

pytestmark = [pytest.mark.unit, pytest.mark.contract]

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACT = REPOSITORY / "contracts" / "registry.yaml"


class InProcessApi:
    """Calls the ASGI app in-process through httpx (no network, no server)."""

    def __init__(self) -> None:
        self._runner = asyncio.Runner()
        transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
        self._client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    def request(self, method: str, url: str) -> httpx.Response:
        """One request; the query string goes in `url`."""
        return self._runner.run(self._client.request(method, url))

    def close(self) -> None:
        """Close the client and the event loop."""
        self._runner.run(self._client.aclose())
        self._runner.close()


@pytest.fixture
def api() -> Iterator[InProcessApi]:
    client = InProcessApi()
    yield client
    client.close()


def health_schema() -> dict[str, Any]:
    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    schema: dict[str, Any] = document["components"]["schemas"]["Health"]
    return schema


def test_health_is_the_contract_schema_without_the_test_double_flag(api: InProcessApi) -> None:
    response = api.request("GET", "/healthz")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    Draft202012Validator(health_schema()).validate(body)
    assert body == {"status": "ok", "service": "registry"}
    assert set(body) <= set(health_schema()["properties"])


def test_health_ignores_a_query_string(api: InProcessApi) -> None:
    assert api.request("GET", "/healthz?probe=2").status_code == 200


@pytest.mark.parametrize("path", ["/healthz/", "/docs", "/redoc", "/openapi.json", "/v1/submissions/"])
def test_a_trailing_slash_or_documentation_path_is_not_found_never_a_redirect(
    api: InProcessApi, path: str
) -> None:
    response = api.request("GET", path)
    assert response.status_code == 404
    assert "location" not in response.headers
