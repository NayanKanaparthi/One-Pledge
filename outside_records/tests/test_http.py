"""The HTTP API against `contracts/outside_records.yaml`, on the seeded session database."""

from __future__ import annotations

import csv
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import jsonschema  # type: ignore[import-untyped]  # jsonschema ships no type information; dev only
import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML ships no type information; types-PyYAML is not allowed
from fastapi.routing import APIRoute
from sqlalchemy import Engine

from onepledge_outside_records.infrastructure.db.engines import make_engine
from onepledge_outside_records.infrastructure.db.repositories import SqlRecordsReader
from onepledge_outside_records.interface.http.app import create_app

if TYPE_CHECKING:
    from conftest import InProcessApi

pytestmark = [pytest.mark.integration, pytest.mark.contract]

Call = Callable[..., httpx.Response]
UNSEEDED_VIN = "1HGCM82633A004352"


class Contract:
    """Validates bodies against the component schemas of the contract."""

    def __init__(self, path: Path) -> None:
        self._components = yaml.safe_load(path.read_text(encoding="utf-8"))["components"]

    def errors(self, schema: str, body: object) -> list[str]:
        """Every validation error of `body` against `#/components/schemas/<schema>`."""
        document = {"$ref": f"#/components/schemas/{schema}", "components": self._components}
        validator = jsonschema.Draft202012Validator(document, format_checker=jsonschema.FormatChecker())
        return [error.message for error in validator.iter_errors(body)]


@pytest.fixture(scope="module")
def contract(contract_path: Path) -> Contract:
    return Contract(contract_path)


@pytest.fixture(scope="module")
def rows(example_records: Path) -> dict[str, list[dict[str, str]]]:
    """The seeded example records, read straight from the CSV files."""
    found = {}
    for name in ("titles", "payments", "contracts"):
        with (example_records / f"{name}.csv").open(encoding="utf-8", newline="") as handle:
            found[name] = list(csv.DictReader(handle))
    return found


@pytest.fixture
def auth(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


@pytest.fixture
def authorised(call: Call, auth: dict[str, str]) -> Call:
    """`call` with the session client's key."""

    def send(method: str, url: str) -> httpx.Response:
        return call(method, url, headers=auth)

    return send


@pytest.fixture
def seeded_vin(rows: dict[str, list[dict[str, str]]]) -> str:
    """The VIN of the first seeded title record."""
    return rows["titles"][0]["vin"]


def busiest_vin(rows: dict[str, list[dict[str, str]]]) -> str:
    counts: dict[str, int] = {}
    for payment in rows["payments"]:
        counts[payment["vin"]] = counts.get(payment["vin"], 0) + 1
    return max(sorted(counts), key=lambda vin: counts[vin])


def assert_error(response: httpx.Response, contract: Contract, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    body: dict[str, Any] = response.json()
    assert contract.errors("ErrorResponse", body) == []
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    return body


def test_a_title_is_returned_as_recorded(
    call: Call, auth: dict[str, str], contract: Contract, rows: dict[str, list[dict[str, str]]]
) -> None:
    for record in rows["titles"]:
        response = call("GET", f"/v1/titles/{record['vin']}", headers=auth)
        assert response.status_code == 200
        body = response.json()
        assert contract.errors("TitleRecord", body) == []
        assert body["lienholder_name"] == (record["lienholder_name"] or None)
        assert body["lien_recorded_on"] == (record["lien_recorded_on"] or None)
        assert (body["state"], body["title_status"]) == (record["state"], record["title_status"])


def test_payments_are_newest_first_with_the_total(
    call: Call, auth: dict[str, str], contract: Contract, rows: dict[str, list[dict[str, str]]]
) -> None:
    vin = busiest_vin(rows)
    expected = sorted(
        (p for p in rows["payments"] if p["vin"] == vin),
        key=lambda p: (p["paid_on"], uuid.UUID(p["payment_id"]).int * -1),
        reverse=True,
    )
    response = call("GET", f"/v1/payments?vin={vin}&limit=200", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert contract.errors("PaymentPage", body) == []
    assert (body["total"], body["limit"], body["offset"]) == (len(expected), 200, 0)
    assert [p["payment_id"] for p in body["items"]] == [p["payment_id"] for p in expected]
    assert [p["amount_cents"] for p in body["items"]] == [int(p["amount_cents"]) for p in expected]


def test_payment_pages_cover_every_payment_once(
    call: Call, auth: dict[str, str], rows: dict[str, list[dict[str, str]]]
) -> None:
    vin = busiest_vin(rows)
    whole = call("GET", f"/v1/payments?vin={vin}&limit=200", headers=auth).json()["items"]
    paged: list[dict[str, Any]] = []
    for offset in range(0, len(whole) + 3, 3):
        page = call("GET", f"/v1/payments?vin={vin}&limit=3&offset={offset}", headers=auth).json()
        assert page["total"] == len(whole)
        paged.extend(page["items"])
    assert paged == whole


def test_the_date_window_is_inclusive(
    call: Call, auth: dict[str, str], rows: dict[str, list[dict[str, str]]]
) -> None:
    vin = busiest_vin(rows)
    dates = sorted(p["paid_on"] for p in rows["payments"] if p["vin"] == vin)
    first, last = dates[1], dates[-2]
    body = call("GET", f"/v1/payments?vin={vin}&from={first}&to={last}&limit=200", headers=auth).json()
    assert body["total"] == len(dates) - 2
    assert {p["paid_on"] for p in body["items"]} == set(dates[1:-1])
    empty = call("GET", f"/v1/payments?vin={vin}&from={last}&to={first}", headers=auth).json()
    assert (empty["items"], empty["total"]) == ([], 0)


def test_a_vehicle_without_payments_gets_an_empty_page(
    call: Call, auth: dict[str, str], contract: Contract
) -> None:
    body = call("GET", f"/v1/payments?vin={UNSEEDED_VIN}", headers=auth).json()
    assert contract.errors("PaymentPage", body) == []
    assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_contracts_are_oldest_first(
    call: Call, auth: dict[str, str], contract: Contract, rows: dict[str, list[dict[str, str]]]
) -> None:
    for vin in {c["vin"] for c in rows["contracts"]}:
        expected = sorted(
            (c for c in rows["contracts"] if c["vin"] == vin),
            key=lambda c: (c["signed_on"], uuid.UUID(c["contract_id"]).int),
        )
        response = call("GET", f"/v1/contracts/{vin}", headers=auth)
        assert response.status_code == 200
        body = response.json()
        assert contract.errors("ContractList", body) == []
        assert [c["contract_id"] for c in body["contracts"]] == [c["contract_id"] for c in expected]


def test_unknown_vehicles_get_their_not_found_codes(
    call: Call, auth: dict[str, str], contract: Contract
) -> None:
    title = call("GET", f"/v1/titles/{UNSEEDED_VIN}", headers=auth)
    assert_error(title, contract, 404, "TITLE_NOT_FOUND")
    contracts = call("GET", f"/v1/contracts/{UNSEEDED_VIN}", headers=auth)
    assert_error(contracts, contract, 404, "CONTRACT_NOT_FOUND")
    assert UNSEEDED_VIN not in title.text + contracts.text


@pytest.mark.parametrize("url", ["/v1/titles/{vin}", "/v1/payments?vin={vin}", "/v1/contracts/{vin}"])
@pytest.mark.parametrize("key", [None, "", "opk_" + "x" * 43, "not-a-key"])
def test_a_missing_or_unknown_key_gets_401(
    call: Call, contract: Contract, rows: dict[str, list[dict[str, str]]], url: str, key: str | None
) -> None:
    headers = {} if key is None else {"X-API-Key": key}
    vin = rows["titles"][0]["vin"]
    response = call("GET", url.format(vin=vin), headers=headers)
    body = assert_error(response, contract, 401, "UNAUTHENTICATED")
    assert vin not in response.text
    assert "details" not in body["error"]


@pytest.mark.parametrize(
    "vin",
    ["1HGCM82633A00435", "1HGCM82633A0043521", "1hgcm82633a004352", "1HGCM82633A00435O", "CANARYVIN1234567"],
)
def test_a_malformed_vin_gets_422_without_echo(
    call: Call, auth: dict[str, str], contract: Contract, vin: str
) -> None:
    for url in (f"/v1/titles/{vin}", f"/v1/payments?vin={vin}", f"/v1/contracts/{vin}"):
        response = call("GET", url, headers=auth)
        body = assert_error(response, contract, 422, "VALIDATION_ERROR")
        assert body["error"]["details"] == [{"field": "vin", "issue": "PATTERN_MISMATCH"}]
        assert vin not in response.text
        assert vin.lower() not in response.text.lower()


@pytest.mark.parametrize(
    ("query", "details"),
    [
        ("", [{"field": "vin", "issue": "MISSING"}]),
        ("&limit=0", [{"field": "limit", "issue": "OUT_OF_RANGE"}]),
        ("&limit=201", [{"field": "limit", "issue": "OUT_OF_RANGE"}]),
        ("&limit=ten", [{"field": "limit", "issue": "INVALID_INTEGER"}]),
        ("&offset=-1", [{"field": "offset", "issue": "INVALID_INTEGER"}]),
        ("&from=2025-02-30", [{"field": "from", "issue": "INVALID_DATE"}]),
        ("&to=20250101", [{"field": "to", "issue": "INVALID_DATE"}]),
        (
            "&from=yesterday&limit=999",
            [{"field": "from", "issue": "INVALID_DATE"}, {"field": "limit", "issue": "OUT_OF_RANGE"}],
        ),
    ],
)
def test_bad_payment_parameters_are_named_without_echo(
    authorised: Call, contract: Contract, seeded_vin: str, query: str, details: list[dict[str, str]]
) -> None:
    prefix = f"vin={seeded_vin}" if query else ""
    response = authorised("GET", f"/v1/payments?{prefix}{query}")
    body = assert_error(response, contract, 422, "VALIDATION_ERROR")
    assert body["error"]["details"] == details
    shown = response.text.replace(body["error"]["request_id"], "")
    for value in ("2025-02-30", "20250101", "yesterday", "ten", "999"):
        assert value not in shown


def test_the_limit_boundaries_are_accepted(
    call: Call, auth: dict[str, str], rows: dict[str, list[dict[str, str]]]
) -> None:
    vin = busiest_vin(rows)
    assert len(call("GET", f"/v1/payments?vin={vin}&limit=1", headers=auth).json()["items"]) == 1
    assert call("GET", f"/v1/payments?vin={vin}&limit=200", headers=auth).status_code == 200


def test_health_needs_no_key(call: Call, contract: Contract) -> None:
    response = call("GET", "/healthz")
    assert response.status_code == 200
    assert contract.errors("Health", response.json()) == []


def test_request_ids_are_echoed_or_replaced(call: Call, auth: dict[str, str]) -> None:
    own = str(uuid.uuid4())
    assert call("GET", "/healthz", headers={"X-Request-ID": own}).headers["x-request-id"] == own
    replaced = call("GET", f"/v1/titles/{UNSEEDED_VIN}", headers={**auth, "X-Request-ID": "<script>"})
    assert uuid.UUID(replaced.headers["x-request-id"]).version == 4
    assert replaced.json()["error"]["request_id"] == replaced.headers["x-request-id"]
    assert "<script>" not in replaced.text


def test_unknown_routes_and_methods_get_the_error_envelope(call: Call, auth: dict[str, str]) -> None:
    missing = call("GET", f"/v1/owners/{UNSEEDED_VIN}", headers=auth)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert UNSEEDED_VIN not in missing.text
    wrong = call("POST", f"/v1/titles/{UNSEEDED_VIN}", headers=auth)
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (405, "METHOD_NOT_ALLOWED")
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert call("GET", path).status_code == 404


@pytest.fixture
def unreachable_api(call_app: Callable[..., InProcessApi]) -> Iterator[InProcessApi]:
    engine = make_engine("postgresql://nobody:pw@127.0.0.1:1/none")
    yield call_app(create_app(SqlRecordsReader(engine)))
    engine.dispose()


def test_a_database_outage_gets_503(unreachable_api: InProcessApi, contract: Contract) -> None:
    response = unreachable_api.request("GET", f"/v1/titles/{UNSEEDED_VIN}", headers={"X-API-Key": "opk_x"})
    assert_error(response, contract, 503, "SERVICE_UNAVAILABLE")
    assert "127.0.0.1" not in response.text


def test_the_routes_equal_the_contract_paths(contract_path: Path, app_engine: Engine) -> None:
    paths = yaml.safe_load(contract_path.read_text(encoding="utf-8"))["paths"]
    documented = {(method.upper(), path) for path, item in paths.items() for method in item}
    app = create_app(SqlRecordsReader(app_engine))
    served = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    assert served == documented
