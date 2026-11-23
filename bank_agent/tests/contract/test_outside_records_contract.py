"""The outside records client against `contracts/outside_records.yaml` and its committed examples.

Every request the client sends is matched to an operation of the YAML: method, path
template, path parameter pattern, query parameter names and bounds, required
parameters and the `X-API-Key` header. Every committed example answer parses.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from onepledge_bank_agent.domain.vin import Vin
from onepledge_bank_agent.infrastructure.http.base import CallPolicy, RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.outside_records import OutsideRecordsClient
from onepledge_bank_agent.infrastructure.request_context import bound_request_id

pytestmark = pytest.mark.contract

VIN = Vin("1HGCM82633A004352")
KEY = "opk_" + "c" * 43
REQUEST_ID = uuid.UUID("8f14e45f-ceea-467f-a8f2-5f6f8b6f1b2a")


@dataclass(frozen=True)
class Operation:
    """One operation of the YAML, with its parameters resolved."""

    method: str
    template: str
    parameters: tuple[dict[str, Any], ...]
    security: tuple[str, ...]

    def matches(self, request: httpx.Request) -> dict[str, str] | None:
        """The path parameters when the request is for this operation."""
        if request.method != self.method:
            return None
        pattern = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", self.template) + "$"
        found = re.match(pattern, request.url.path)
        return found.groupdict() if found else None


class Contract:
    """`outside_records.yaml` loaded once."""

    def __init__(self, path: Path) -> None:
        self.document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        default_security = self.document.get("security", [])
        self.operations = [
            Operation(
                method.upper(),
                template,
                tuple(self._resolve(parameter) for parameter in body.get("parameters", [])),
                tuple(name for item in body.get("security", default_security) for name in item),
            )
            for template, item in self.document["paths"].items()
            for method, body in item.items()
        ]

    def _resolve(self, node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            target: Any = self.document
            for part in node["$ref"].removeprefix("#/").split("/"):
                target = target[part]
            node = target
        return node

    def schema(self, parameter: dict[str, Any]) -> dict[str, Any]:
        """A parameter's schema with references resolved."""
        return self._resolve(parameter["schema"])

    def header_name(self, scheme: str) -> str:
        """The header of an apiKey security scheme."""
        found: str = self.document["components"]["securitySchemes"][scheme]["name"]
        return found

    def operation_for(self, request: httpx.Request) -> tuple[Operation, dict[str, str]]:
        """The one operation serving the request."""
        for operation in self.operations:
            path = operation.matches(request)
            if path is not None:
                return operation, path
        raise AssertionError(f"{request.method} {request.url.path} is not an operation of the contract")


@pytest.fixture(scope="module")
def contract(contracts_dir: Path) -> Contract:
    return Contract(contracts_dir / "outside_records.yaml")


@pytest.fixture(scope="module")
def examples(contracts_dir: Path) -> Callable[[str], dict[str, Any]]:
    def load(name: str) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(
            (contracts_dir / "examples" / "outside_records" / name).read_text(encoding="utf-8")
        )
        return loaded

    return load


def conforms(contract: Contract, request: httpx.Request) -> None:
    """Assert that the request is one the contract allows."""
    operation, path = contract.operation_for(request)
    query = dict(request.url.params)
    for parameter in operation.parameters:
        schema, name, place = contract.schema(parameter), parameter["name"], parameter["in"]
        value = {"path": path, "query": query, "header": request.headers}[place].get(name)
        if parameter.get("required"):
            assert value is not None, f"{name} is required"
        if value is None:
            continue
        if "pattern" in schema:
            assert re.fullmatch(schema["pattern"], value), name
        if schema.get("type") == "integer":
            assert schema.get("minimum", 0) <= int(value) <= schema.get("maximum", 2**31), name
        if schema.get("format") == "date":
            date.fromisoformat(value)
    allowed = {p["name"] for p in operation.parameters if p["in"] == "query"}
    assert set(query) <= allowed, f"unknown query parameters {set(query) - allowed}"
    for scheme in operation.security:
        assert request.headers.get(contract.header_name(scheme)) == KEY


class Recorded:
    """A client whose requests are kept and answered from the committed examples."""

    def __init__(self, answer: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._answer = answer

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._answer(request)

    def client(self) -> OutsideRecordsClient:
        http = httpx.Client(base_url="http://outside.test", transport=httpx.MockTransport(self.handle))
        return OutsideRecordsClient(
            RetryingHttpCaller(http, KEY, "outside_records", _Quiet(), CallPolicy(_no_sleep))
        )


class _Quiet:
    def info(self, event: str, **fields: object) -> None:
        """Ignored."""

    def warning(self, event: str, **fields: object) -> None:
        """Ignored."""

    def failure(self, event: str, error: BaseException, **fields: object) -> None:
        """Ignored."""


def _no_sleep(seconds: float) -> None:
    """Retries do not wait."""


@pytest.fixture
def from_examples(examples: Callable[[str], dict[str, Any]]) -> Iterator[Recorded]:
    def answer(request: httpx.Request) -> httpx.Response:
        name = {"titles": "title.response.json", "payments": "payments.response.json"}.get(
            request.url.path.split("/")[2], "contracts.response.json"
        )
        return httpx.Response(200, json=examples(name))

    yield Recorded(answer)


def test_every_request_of_the_client_is_an_operation_of_the_contract(
    contract: Contract, from_examples: Recorded
) -> None:
    client = from_examples.client()
    with bound_request_id(REQUEST_ID):
        title = client.title(VIN)
        window = client.payments(VIN, date(2026, 8, 1), date(2026, 10, 30))
        contracts = client.contracts(VIN)
    assert title is not None and title.lienholder_name == "SUMMIT AUTO FINANCE, L.L.C."
    assert len(window.payments) == window.total_reported == 2
    assert contracts and all(record.amount_cents > 0 for record in contracts)
    assert [request.url.path for request in from_examples.requests] == [
        f"/v1/titles/{VIN.value}",
        "/v1/payments",
        f"/v1/contracts/{VIN.value}",
    ]
    for request in from_examples.requests:
        conforms(contract, request)
        assert request.headers["X-Request-ID"] == str(REQUEST_ID)


def test_the_committed_not_found_examples_mean_no_record(
    contract: Contract, examples: Callable[[str], dict[str, Any]]
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        name = (
            "title.error.not_found.json" if "titles" in request.url.path else "contracts.error.not_found.json"
        )
        return httpx.Response(404, json=examples(name))

    recorded = Recorded(answer)
    client = recorded.client()
    assert client.title(VIN) is None
    assert client.contracts(VIN) == ()
    for request in recorded.requests:
        conforms(contract, request)


def test_every_status_the_contract_lists_for_the_client_routes_is_handled(contract: Contract) -> None:
    handled = {"200", "401", "404", "422", "429", "503"}
    for operation in contract.operations:
        if operation.template == "/healthz":
            continue
        statuses = set(contract.document["paths"][operation.template][operation.method.lower()]["responses"])
        assert statuses <= handled, (operation.template, statuses - handled)


@pytest.mark.parametrize(
    "request_",
    [
        httpx.Request("GET", "http://o/v1/titles/1HGCM82633A004352"),
        httpx.Request("GET", "http://o/v1/titles/1hgcm82633a004352", headers={"X-API-Key": KEY}),
        httpx.Request("GET", "http://o/v1/payments?from=2026-01-01", headers={"X-API-Key": KEY}),
        httpx.Request("GET", f"http://o/v1/payments?vin={VIN.value}&limit=201", headers={"X-API-Key": KEY}),
        httpx.Request("GET", f"http://o/v1/payments?vin={VIN.value}&page=2", headers={"X-API-Key": KEY}),
        httpx.Request("POST", "http://o/v1/contracts/1HGCM82633A004352", headers={"X-API-Key": KEY}),
    ],
    ids=["no key", "lower case vin", "no vin", "limit too high", "unknown parameter", "wrong method"],
)
def test_the_conformance_check_refuses_requests_outside_the_contract(
    contract: Contract, request_: httpx.Request
) -> None:
    with pytest.raises(AssertionError):
        conforms(contract, request_)
