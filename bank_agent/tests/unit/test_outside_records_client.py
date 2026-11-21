"""The outside records clients over a mocked network boundary (SPEC 11.4, 16.6)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import date

import httpx
import pytest

from onepledge_bank_agent.domain import Vin
from onepledge_bank_agent.domain.errors import (
    OutsideRecordsProtocolError,
    OutsideRecordsRejectedError,
    OutsideRecordsUnavailableError,
)
from onepledge_bank_agent.infrastructure.http.base import RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.outside_records import OutsideRecordsClient
from onepledge_bank_agent.infrastructure.request_context import bound_request_id

pytestmark = pytest.mark.unit

VIN = Vin("1HGCM82633A004352")
KEY = "opk_" + "a" * 43
TITLE = {
    "vin": VIN.value,
    "state": "NJ",
    "title_status": "CLEAN",
    "lienholder_name": "Summit Auto Finance LLC",
    "lien_recorded_on": "2025-04-01",
}
CONTRACTS = {
    "vin": VIN.value,
    "contracts": [
        {
            "contract_id": str(uuid.uuid4()),
            "vin": VIN.value,
            "lender_name": "Summit Auto Finance LLC",
            "amount_cents": 100,
            "signed_on": "2025-03-15",
            "document_sha256": "a" * 64,
        }
    ],
}


class Recorder:
    """Records events and sleeps the caller reports."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.sleeps: list[float] = []

    def info(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def warning(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def failure(self, event: str, error: BaseException, **fields: object) -> None:
        self.events.append((event, fields))


def client(
    handler: Callable[[httpx.Request], httpx.Response], recorder: Recorder | None = None
) -> OutsideRecordsClient:
    log = recorder or Recorder()
    http = httpx.Client(base_url="http://outside.test", transport=httpx.MockTransport(handler))
    return OutsideRecordsClient(RetryingHttpCaller(http, KEY, "outside_records", log, log.sleeps.append))


def answer(status: int, body: object) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, json=body)


def error(code: str) -> dict[str, object]:
    return {"error": {"code": code, "message": "x.", "request_id": "r"}}


def test_a_title_is_parsed_and_the_key_is_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={**TITLE, "added_later": "ignored"})

    record = client(handler).title(VIN)
    assert (
        record is not None
        and record.lienholder_name == "Summit Auto Finance LLC"
        and record.lien_recorded_on == date(2025, 4, 1)
    )
    assert seen[0].url.path == f"/v1/titles/{VIN.value}" and seen[0].headers["X-API-Key"] == KEY
    assert "X-Request-ID" not in seen[0].headers


def test_the_bound_request_id_is_forwarded() -> None:
    seen: list[httpx.Request] = []
    request_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=CONTRACTS)

    with bound_request_id(request_id):
        assert len(client(handler).contracts(VIN)) == 1
    assert seen[0].headers["X-Request-ID"] == str(request_id)


def test_not_found_means_no_record_only_with_the_agreed_code() -> None:
    assert client(answer(404, error("TITLE_NOT_FOUND"))).title(VIN) is None
    assert client(answer(404, error("CONTRACT_NOT_FOUND"))).contracts(VIN) == ()
    with pytest.raises(OutsideRecordsProtocolError):
        client(answer(404, error("NOT_FOUND"))).title(VIN)
    with pytest.raises(OutsideRecordsProtocolError):
        client(lambda request: httpx.Response(404, text="<html>")).contracts(VIN)


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, OutsideRecordsRejectedError),
        (422, OutsideRecordsRejectedError),
        (403, OutsideRecordsProtocolError),
        (405, OutsideRecordsProtocolError),
        (429, OutsideRecordsUnavailableError),
        (500, OutsideRecordsUnavailableError),
    ],
)
def test_status_codes_map_to_permanent_or_transient_errors(status: int, error_type: type[Exception]) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, json=error("X"))

    with pytest.raises(error_type):
        client(handler).title(VIN)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [502, 503, 504])
def test_gateway_errors_are_retried_twice_then_transient(status: int) -> None:
    recorder = Recorder()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status)

    with pytest.raises(OutsideRecordsUnavailableError):
        client(handler, recorder).title(VIN)
    assert len(calls) == 3 and recorder.sleeps == [0.2, 0.4]
    routes = {fields["route"] for _, fields in recorder.events}
    assert routes == {"/v1/titles/{vin}"} and VIN.value not in repr(recorder.events)


def test_a_transport_error_is_retried_and_a_later_success_is_used() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json=TITLE)

    assert client(handler).title(VIN) is not None and len(attempts) == 3
    with pytest.raises(OutsideRecordsUnavailableError):
        client(lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request))).title(VIN)


@pytest.mark.parametrize(
    "body",
    [
        {**TITLE, "vin": "1HGCM82633A004353"},
        {**TITLE, "title_status": "NEW"},
        {"vin": VIN.value},
        "not json",
        {**TITLE, "state": "nj"},
    ],
)
def test_a_malformed_or_foreign_title_is_a_protocol_error(body: object) -> None:
    handler = (lambda r: httpx.Response(200, text=str(body))) if isinstance(body, str) else answer(200, body)
    with pytest.raises(OutsideRecordsProtocolError):
        client(handler).title(VIN)


def test_payments_are_requested_for_the_window_and_checked_for_the_vin() -> None:
    seen: list[httpx.Request] = []
    item = {
        "payment_id": str(uuid.uuid4()),
        "vin": VIN.value,
        "paid_on": "2026-10-01",
        "amount_cents": 500,
        "deposit_bank_code": "HARBOR",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [item], "total": 1, "limit": 200, "offset": 0})

    window = client(handler).payments(VIN, date(2026, 7, 17), date(2026, 10, 15))
    assert window.total_reported == 1 and window.payments[0].deposit_bank_code == "HARBOR"
    assert dict(seen[0].url.params) == {
        "vin": VIN.value,
        "from": "2026-07-17",
        "to": "2026-10-15",
        "limit": "200",
    }
    foreign = {"items": [{**item, "vin": "5YJ3E1EA2KF317000"}], "total": 1, "limit": 200, "offset": 0}
    with pytest.raises(OutsideRecordsProtocolError):
        client(answer(200, foreign)).payments(VIN, date(2026, 7, 17), date(2026, 10, 15))
    with pytest.raises(OutsideRecordsProtocolError):
        client(answer(200, {**CONTRACTS, "contracts": []})).contracts(VIN)
    assert json.loads(json.dumps(item))["vin"] == VIN.value
