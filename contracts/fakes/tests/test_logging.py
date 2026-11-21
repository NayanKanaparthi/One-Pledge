"""Structured logging of the doubles: allow-list, value shapes and the VIN scrub (SPEC 16.5 rules)."""

from __future__ import annotations

import json
import logging

import pytest

from onepledge_contract_fakes.logging_setup import (
    ALLOWED_KEYS,
    REDACTED,
    LogEvent,
    RedactionProcessor,
    configure_logging,
    get_logger,
    scrub_vin_shapes,
)

pytestmark = pytest.mark.unit

SPEC_ALLOW_LIST = {
    "ts", "level", "event", "service", "bank_code", "request_id", "logger", "submission_id",
    "loan_id", "alert_id", "registry_request_id", "certificate_id", "checkpoint_seq", "user_id",
    "role", "stage", "status", "kind", "count", "rows", "attempts", "duration_ms", "http_status",
    "method", "route", "error_code", "failure_code", "check_type", "result", "reason_code",
    "batch_index", "item_count", "exception_type", "stack", "dependency",
}  # fmt: skip


def process(event: dict[str, object]) -> dict[str, object]:
    return dict(RedactionProcessor()(None, "info", dict(event)))


def test_allow_list_is_exactly_the_spec_list() -> None:
    assert set(ALLOWED_KEYS) == SPEC_ALLOW_LIST


def test_disallowed_keys_are_dropped_and_named() -> None:
    result = process({"event": "x", "vin": "1HGCM82633A004352", "borrower": "C1234567", "count": 3})
    assert result == {"event": "x", "count": 3, "redacted_keys": ["borrower", "vin"]}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (7, 7),
        (True, True),
        (None, None),
        ("/v1/titles/{vin}", "/v1/titles/{vin}"),
        ("has,comma", REDACTED),
        ("x" * 129, REDACTED),
        (1.5, REDACTED),
        (["a"], REDACTED),
    ],
)
def test_value_shapes(value: object, expected: object) -> None:
    assert process({"event": "e", "route": value})["route"] == expected


@pytest.mark.parametrize("vin", ["1HGCM82633A004352", "1hgcm82633a004352", "1HgCm82633A004352"])
def test_vin_shapes_are_scrubbed_in_any_case(vin: str) -> None:
    assert process({"event": "e", "route": f"before {vin} after"})["route"] == f"before {REDACTED} after"
    assert REDACTED in scrub_vin_shapes(f"stack {vin}")


def test_seventeen_letters_without_three_digits_are_not_vins() -> None:
    assert scrub_vin_shapes("ABCDEFGHJKLMNPRST") == "ABCDEFGHJKLMNPRST"


def test_configured_logger_writes_one_json_object_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("registry-fake", "INFO")
    logger = get_logger("tests")
    logger.info(LogEvent.HTTP_REQUEST, route="/v1/outbox", http_status=200, vin="1HGCM82633A004352")
    try:
        raise RuntimeError("message with 1HGCM82633A004352")
    except RuntimeError:
        logger.error(LogEvent.UNHANDLED_EXCEPTION, exc_info=True)
    logging.getLogger("uvicorn.error").warning("library text 1HGCM82633A004352")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["event"] for line in lines] == ["http_request", "unhandled_exception", "library_log"]
    assert all({"ts", "level", "event", "service"} <= set(line) for line in lines)
    assert lines[0]["redacted_keys"] == ["vin"] and lines[0]["service"] == "registry-fake"
    assert lines[1]["exception_type"] == "RuntimeError" and "test_logging.py" in lines[1]["stack"]
    assert lines[2]["logger"] == "uvicorn.error"
    assert "1HGCM82633A004352" not in json.dumps(lines) and "message with" not in json.dumps(lines)
