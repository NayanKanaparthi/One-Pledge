"""Log records: route templates only, no VIN, no path or query string, no exception text (SPEC 6.2, 16.5)."""

from __future__ import annotations

import ast
import json
import logging
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import structlog
from sqlalchemy import Engine

from onepledge_outside_records.domain.records import PaymentPage, PaymentQuery
from onepledge_outside_records.infrastructure.db.repositories import SqlRecordsReader
from onepledge_outside_records.infrastructure.logging_setup import (
    ALLOWED_KEYS,
    REDACTED,
    RedactionProcessor,
    configure_logging,
    scrub_vin_shapes,
)
from onepledge_outside_records.interface.http.app import create_app

if TYPE_CHECKING:
    from conftest import InProcessApi

Call = Callable[..., httpx.Response]
CANARY_VIN = "5YJ3E1EA7KF317000"
SPEC_16_5_KEYS = {
    "ts", "level", "event", "service", "bank_code", "request_id", "logger", "submission_id",
    "loan_id", "alert_id", "registry_request_id", "certificate_id", "checkpoint_seq", "user_id",
    "role", "stage", "status", "kind", "count", "rows", "attempts", "duration_ms", "http_status",
    "method", "route", "error_code", "failure_code", "check_type", "result", "reason_code",
    "batch_index", "item_count", "exception_type", "stack", "dependency",
}  # fmt: skip


@pytest.fixture
def log_lines(capsys: pytest.CaptureFixture[str]) -> Iterator[Callable[[], list[dict[str, Any]]]]:
    """A reader of the JSON lines written to stdout. Its first call routes logging to the captured stdout."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    configured: list[bool] = []

    def read() -> list[dict[str, Any]]:
        if not configured:
            configure_logging("outside-records", "DEBUG")
            configured.append(True)
        return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]

    yield read
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


class ExplodingReader(SqlRecordsReader):
    """A reader whose payment lookup fails with a message that holds a VIN."""

    def payments(self, query: PaymentQuery) -> PaymentPage:
        message = f"lookup of {query.vin.value} failed"
        raise RuntimeError(message)


SOURCE = Path(__file__).resolve().parents[1] / "src" / "onepledge_outside_records"
LOG_METHODS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})
# Keys every record gets from the processors rather than from a call site.
PROCESSOR_KEYS = frozenset({"ts", "level", "event", "service", "request_id", "logger"})
EXCEPTION_KEYS = frozenset({"exception_type", "stack"})


def keys_written_by_the_source() -> set[str]:
    """Keyword names passed to log calls anywhere in the package."""
    found: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in LOG_METHODS
            ):
                found.update(k.arg for k in node.keywords if k.arg is not None and k.arg != "exc_info")
    return found


@pytest.mark.unit
def test_the_allow_list_is_the_spec_keys_this_service_writes_plus_client() -> None:
    written = keys_written_by_the_source()
    assert set(ALLOWED_KEYS) == PROCESSOR_KEYS | EXCEPTION_KEYS | written
    assert set(ALLOWED_KEYS) - {"client"} <= SPEC_16_5_KEYS
    assert "client" in ALLOWED_KEYS
    assert "user_id" not in ALLOWED_KEYS


@pytest.mark.unit
def test_disallowed_keys_are_dropped_and_named() -> None:
    record = RedactionProcessor()(
        None,
        "info",
        {"event": "x", "vin": CANARY_VIN, "path": "/v1/titles/x", "user_id": "u", "client": "harbor"},
    )
    assert record == {"event": "x", "client": "harbor", "redacted_keys": ["path", "user_id", "vin"]}


@pytest.mark.unit
@pytest.mark.parametrize("vin", [CANARY_VIN, CANARY_VIN.lower(), "5yJ3e1Ea7Kf317000"])
def test_vin_shapes_are_scrubbed_in_any_case(vin: str) -> None:
    assert scrub_vin_shapes(f"route {vin} done") == f"route {REDACTED} done"
    record = RedactionProcessor()(
        None, "info", {"event": "x", "route": f"/v1/{vin}", "stack": f"a.py:1 {vin}"}
    )
    assert vin not in json.dumps(record)


@pytest.mark.unit
def test_unsafe_values_are_redacted() -> None:
    record = RedactionProcessor()(
        None,
        "info",
        {
            "event": "x",
            "route": "a?b=c",
            "http_status": 3.5,
            "method": "x" * 129,
            "client": None,
            "logger": "a,b",
        },
    )
    assert record == {
        "event": "x",
        "route": REDACTED,
        "http_status": REDACTED,
        "method": REDACTED,
        "client": None,
        "logger": REDACTED,
    }


@pytest.mark.integration
def test_access_records_carry_the_route_template_and_never_the_vin(
    call: Call, api_key: str, log_lines: Callable[[], list[dict[str, Any]]]
) -> None:
    log_lines()
    auth = {"X-API-Key": api_key}
    urls = [
        f"/v1/titles/{CANARY_VIN}",
        f"/v1/payments?vin={CANARY_VIN}&from=2025-01-01&to=2025-12-31",
        f"/v1/contracts/{CANARY_VIN}",
        f"/v1/titles/{CANARY_VIN.lower()}",
        f"/v1/payments?vin={CANARY_VIN}&limit=999",
    ]
    for url in urls:
        call("GET", url, headers=auth)
    call("GET", f"/v1/titles/{CANARY_VIN}")
    records = [r for r in log_lines() if r["event"] == "http_request"]
    assert [r["route"] for r in records] == [
        "/v1/titles/{vin}",
        "/v1/payments",
        "/v1/contracts/{vin}",
        "/v1/titles/{vin}",
        "/v1/payments",
        "/v1/titles/{vin}",
    ]
    assert [r["http_status"] for r in records] == [404, 200, 404, 422, 422, 401]
    assert all(r["method"] == "GET" and r["service"] == "outside-records" for r in records)
    assert [r["client"] for r in records] == ["harbor"] * 5 + [None]
    assert not any("user_id" in r or "redacted_keys" in r for r in records)
    for record in records:
        assert uuid.UUID(record.pop("request_id")).version == 4
        record.pop("ts")
    text = json.dumps(records)
    for forbidden in (CANARY_VIN, CANARY_VIN.lower(), "2025-01-01", "2025-12-31", "?", "999", api_key):
        assert forbidden not in text


@pytest.mark.integration
def test_an_unexpected_failure_logs_the_type_and_frames_only(
    app_engine: Engine,
    api_key: str,
    call_app: Callable[..., InProcessApi],
    log_lines: Callable[[], list[dict[str, Any]]],
) -> None:
    api = call_app(create_app(ExplodingReader(app_engine)))
    log_lines()
    response = api.request("GET", f"/v1/payments?vin={CANARY_VIN}", headers={"X-API-Key": api_key})
    assert (response.status_code, response.json()["error"]["code"]) == (500, "INTERNAL_ERROR")
    records = log_lines()
    failure = next(r for r in records if r["event"] == "unhandled_exception")
    assert failure["exception_type"] == "RuntimeError"
    assert "test_logging.py" in failure["stack"]
    text = json.dumps(records) + response.text
    assert CANARY_VIN not in text
    assert "failed" not in text


@pytest.mark.integration
def test_library_records_are_redacted_too(log_lines: Callable[[], list[dict[str, Any]]]) -> None:
    log_lines()
    logging.getLogger("sqlalchemy.engine").warning("parameters %s", CANARY_VIN)
    record = log_lines()[-1]
    assert record["event"] == "library_log"
    assert record["logger"] == "sqlalchemy.engine"
    assert CANARY_VIN not in json.dumps(record)
