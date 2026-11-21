"""`RedactionProcessor` and the logging setup of SPEC 16.5."""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.infrastructure.logging import (
    ALLOWED_KEYS,
    REDACTED,
    RedactionProcessor,
    StructlogOperationalLog,
    configure_logging,
    scrub_vin_shapes,
)
from onepledge_bank_agent.infrastructure.request_context import bound_request_id, current_request_id

pytestmark = pytest.mark.unit

SPEC_16_5_KEYS = {
    "ts", "level", "event", "service", "bank_code", "request_id", "logger", "submission_id",
    "loan_id", "alert_id", "registry_request_id", "certificate_id", "checkpoint_seq", "user_id",
    "role", "stage", "status", "kind", "count", "rows", "attempts", "duration_ms", "http_status",
    "method", "route", "error_code", "failure_code", "check_type", "result", "reason_code",
    "batch_index", "item_count", "exception_type", "stack", "dependency",
}  # fmt: skip
REDACT = RedactionProcessor()


def redact(record: dict[str, Any]) -> dict[str, Any]:
    return dict(REDACT(None, "info", record))


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    """The product's logging configuration writing into a buffer; restored afterwards."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    stream = io.StringIO()
    configure_logging("HARBOR", "DEBUG", stream)
    yield stream
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()


def lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_the_allow_list_is_exactly_the_spec_list() -> None:
    assert ALLOWED_KEYS == SPEC_16_5_KEYS


def test_a_disallowed_key_is_dropped_and_named() -> None:
    out = redact({"event": "tape_received", "vin": "1HGCM82633A004352", "borrower": "C1", "count": 3})
    assert out == {"event": "tape_received", "count": 3, "redacted_keys": ["borrower", "vin"]}


@pytest.mark.parametrize(
    ("value", "kept"),
    [(7, True), (True, True), (None, True), ("SUBMITTED", True), ("/v1/titles/{vin}", True),
     ("a" * 128, True), ("a" * 129, False), ("x=1", False), ("é", False), ("line\nbreak", False),
     (1.5, False), (["a"], False), ({"a": 1}, False)],
)  # fmt: skip
def test_value_shapes(value: object, kept: bool) -> None:
    out = redact({"status": value})
    assert out["status"] == (value if kept else REDACTED)


@pytest.mark.parametrize("vin", ["1HGCM82633A004352", "1hgcm82633a004352", "1HgCm82633a004352"])
def test_vin_shaped_runs_are_scrubbed_in_any_case(vin: str) -> None:
    assert redact({"error_code": f"bad {vin} here"})["error_code"] == f"bad {REDACTED} here"
    assert scrub_vin_shapes(vin) == REDACTED


def test_runs_that_are_not_vin_shaped_stay() -> None:
    assert scrub_vin_shapes("ABCDEFGHJKLMNPRST") == "ABCDEFGHJKLMNPRST"
    assert scrub_vin_shapes("1HGCM82633A00435") == "1HGCM82633A00435"
    assert scrub_vin_shapes("x1HGCM82633A004352x") == "x1HGCM82633A004352x"


def test_the_stack_may_be_long_and_multi_line_but_is_scrubbed() -> None:
    stack = (
        "\n".join(f"/src/module_{i}.py:{i} function_{i}" for i in range(20)) + "\n/x/1HGCM82633A004352.py:1 f"
    )
    out = redact({"stack": stack})["stack"]
    assert len(out) > 128 and "1HGCM82633A004352" not in out and REDACTED in out


def test_records_are_json_with_fixed_keys_and_no_exception_text(captured: io.StringIO) -> None:
    log = StructlogOperationalLog("test")
    log.info(LogEvent.TAPE_RECEIVED, submission_id=str(uuid.uuid4()), rows=3)
    try:
        raise ValueError("1HGCM82633A004352 belongs to C1234567")
    except ValueError as error:
        log.failure(LogEvent.STAGE_UNEXPECTED_ERROR, error, stage="CHECKED")
    first, second = lines(captured)
    assert {"ts", "level", "event", "service", "bank_code"} <= set(first)
    assert (first["service"], first["bank_code"], first["event"], first["rows"]) == (
        "bank-agent",
        "HARBOR",
        "tape_received",
        3,
    )
    assert second["exception_type"] == "ValueError" and "test_logging.py" in second["stack"]
    text = captured.getvalue()
    assert "1HGCM82633A004352" not in text and "C1234567" not in text and "belongs" not in text


def test_standard_library_records_keep_only_their_logger_name(captured: io.StringIO) -> None:
    logging.getLogger("sqlalchemy.engine").warning("SELECT * FROM loans WHERE vin = %s", "1HGCM82633A004352")
    (record,) = lines(captured)
    assert record["event"] == "library_log" and record["logger"] == "sqlalchemy.engine"
    assert "1HGCM82633A004352" not in captured.getvalue() and "SELECT" not in captured.getvalue()


def test_quiet_loggers_and_the_bound_request_id(captured: io.StringIO) -> None:
    logging.getLogger("httpx").info("GET /v1/titles/1HGCM82633A004352")
    assert captured.getvalue() == ""
    request = uuid.uuid4()
    assert current_request_id() is None
    with bound_request_id(request):
        StructlogOperationalLog("test").info(LogEvent.TAPE_RECEIVED)
    assert current_request_id() is None
    assert lines(captured)[0]["request_id"] == str(request)


def test_an_unknown_key_logged_through_the_product_logger_is_named_not_shown(captured: io.StringIO) -> None:
    structlog.stdlib.get_logger("probe").info("probe", vin="1HGCM82633A004352", colour="red")
    (record,) = lines(captured)
    assert record["redacted_keys"] == ["colour", "vin"] and "1HGCM82633A004352" not in captured.getvalue()
