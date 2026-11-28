"""`GET /healthz` in process: the contract's `Health` schema, no redirect, no documentation routes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from jsonschema import Draft202012Validator

if TYPE_CHECKING:
    from conftest import Harness

pytestmark = [pytest.mark.unit, pytest.mark.contract]

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACT = REPOSITORY / "contracts" / "key_holder.yaml"


def health_schema() -> dict[str, Any]:
    document = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    schema: dict[str, Any] = document["components"]["schemas"]["Health"]
    return schema


@pytest.mark.parametrize(("holder", "version"), [("kh1", 1), ("kh2", 7)])
def test_health_is_the_contract_schema_with_the_configured_identity(
    make_holder: Callable[..., Harness], holder: str, version: int
) -> None:
    response = make_holder(holder_id=holder, version=version).api.request("GET", "/healthz")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    Draft202012Validator(health_schema()).validate(body)
    assert body == {
        "status": "ok",
        "service": "key_holder",
        "holder_id": holder,
        "active_key_version": version,
    }


def test_health_needs_no_key_and_has_no_test_double_flag(make_holder: Callable[..., Harness]) -> None:
    body = make_holder().api.request("GET", "/healthz").json()
    assert "test_double" not in body


def test_every_field_of_the_body_is_a_property_of_the_schema(make_holder: Callable[..., Harness]) -> None:
    body = make_holder(holder_id="kh2", version=3).api.request("GET", "/healthz").json()
    schema = health_schema()
    assert set(body) <= set(schema["properties"])
    assert set(schema["required"]) <= set(body)


def test_health_ignores_a_query_string(make_holder: Callable[..., Harness]) -> None:
    response = make_holder().api.request("GET", "/healthz?cache=1")
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/healthz/", "/docs", "/redoc", "/openapi.json", "/v1/evaluate/"])
def test_a_trailing_slash_or_documentation_path_is_not_found_never_a_redirect(
    make_holder: Callable[..., Harness], path: str
) -> None:
    response = make_holder().api.request("GET", path)
    assert response.status_code == 404
    assert "location" not in response.headers
