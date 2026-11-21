"""The log canary test of SPEC 16.5 and 21.5 for intake, clean and check.

A tape with known canary values goes through the whole of these stages and through
their failing paths (rejected uploads, a duplicate, a forbidden upload, an outage up
to the attempt limit, a protocol violation, an unexpected exception whose message
carries the values). Every log record of the bank agent and of the outside records
service is captured at DEBUG. No canary value may appear, in any case, in any field
other than `ts`, and no outside records URL with a VIN may appear at all.
"""

from __future__ import annotations

import io
import json
import logging
import re
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest
import structlog

from bank_testkit.clocks import ManualClock
from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase
from bank_testkit.outside import InProcessOutsideRecords, ScriptedOutsideRecords
from bank_testkit.pipeline import BankAgentHarness
from bank_testkit.tapes import summit_row, summit_tape
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.domain.enums import Role, TapeKind
from onepledge_bank_agent.domain.errors import (
    ForbiddenError,
    TapeDuplicateError,
    TapeHeaderInvalidError,
    TapeMalformedError,
)
from onepledge_bank_agent.infrastructure.logging import configure_logging

pytestmark = pytest.mark.integration

FACILITY, AS_OF = "HARBOR-SUMMIT", date(2026, 9, 30)
VALID_VINS = ("2T1BURHE6KC731946", "5NPE24AF1FH791354")
BAD_CHECK_DIGIT_VIN = "JM1BL1SF7A1213785"
BORROWERS = ("C7319468", "C5550123", "C4440777")
LOAN_NUMBERS = ("SAF-7319468-01", "SAF-5550123-02", "SAF-4440777-03")
AMOUNTS_CENTS = (2791346, 2160483, 1937721, 1500209)
DATES = (date(2024, 4, 27), date(2023, 11, 3))
ROWS = (
    summit_row(loan_number=LOAN_NUMBERS[0], vin=VALID_VINS[0].lower(), borrower=BORROWERS[0],
               origination="04/27/2024", original="$27,913.46", balance="$21,604.83"),
    summit_row(loan_number=LOAN_NUMBERS[1], vin=f" {VALID_VINS[1][:8]}-{VALID_VINS[1][8:]} ",
               borrower=BORROWERS[1], origination="11/03/2023", original="$19,377.21", balance="$15,002.09"),
    summit_row(loan_number=LOAN_NUMBERS[2], vin=BAD_CHECK_DIGIT_VIN, borrower=BORROWERS[2],
               origination="13/45/2024", original="$27,913.46", balance="-$21,604.83"),
)  # fmt: skip
OUTSIDE_URL = re.compile(r"/v1/(titles|contracts)/[A-Za-z0-9]{17}|vin=", re.IGNORECASE)


def canary_tokens() -> list[str]:
    """Every canary value in every form it could be written in."""
    vins = [*VALID_VINS, BAD_CHECK_DIGIT_VIN]
    amounts = [
        form
        for cents in AMOUNTS_CENTS
        for form in (str(cents), f"{cents / 100:.2f}", f"{cents / 100:,.2f}", f"${cents / 100:,.2f}")
    ]
    dates = [
        form
        for day in DATES
        for form in (day.isoformat(), day.strftime("%m/%d/%Y"), day.strftime("%Y%m%d"), f"{day:%-m/%-d/%Y}")
    ]
    return [*vins, *BORROWERS, *LOAN_NUMBERS, *amounts, *dates, "13/45/2024"]


def hits(records: list[dict[str, object]]) -> list[tuple[str, str]]:
    """(token, record) for every canary token found as a whole token outside `ts`."""
    patterns = [
        (token, re.compile(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", re.IGNORECASE))
        for token in canary_tokens()
    ]
    found = []
    for record in records:
        text = json.dumps({key: value for key, value in record.items() if key != "ts"})
        found += [(token, text) for token, pattern in patterns if pattern.search(text)]
        if OUTSIDE_URL.search(text):
            found.append(("outside records URL", text))
    return found


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    """All log output of this process (bank agent and in-process outside records) at DEBUG."""
    stream = io.StringIO()
    configure_logging("HARBOR", "DEBUG", stream)
    yield stream
    structlog.reset_defaults()
    logging.getLogger().handlers = []


def records_of(stream: io.StringIO) -> list[dict[str, object]]:
    """Every line is one JSON object."""
    lines = stream.getvalue().splitlines()
    assert lines, "something was logged"
    return [json.loads(line) for line in lines]


def exploding(request: httpx.Request) -> httpx.Response:
    """An unexpected error whose message carries the canary values."""
    raise RuntimeError(f"{VALID_VINS[0]} {BORROWERS[0]} $27,913.46 04/27/2024 {request.url}")


def echoing(request: httpx.Request) -> httpx.Response:
    """A 200 answer that breaks the contract and echoes the request."""
    return httpx.Response(200, json={"vin": str(request.url), "note": BORROWERS[0]})


def test_no_canary_value_reaches_any_log_record_of_intake_clean_and_check(
    captured: io.StringIO,
    new_database: Callable[[], BankDatabase],
    small_data: Path,
    small_outside: InProcessOutsideRecords,
) -> None:
    tape = summit_tape(ROWS)

    def bank() -> BankDatabase:
        database = new_database()
        seed_reference(database, small_data)
        return database

    happy = bank()
    with BankAgentHarness(happy, small_outside) as harness:
        harness.ingest(tape, FACILITY, TapeKind.PLEDGE, AS_OF)
        assert [run.status.value for run in harness.run_worker().runs] == ["CLEANED", "CHECKED"]
        with pytest.raises(TapeDuplicateError):
            harness.ingest(tape, FACILITY, TapeKind.PLEDGE, AS_OF)
        with pytest.raises(ForbiddenError):
            harness.ingest(tape, FACILITY, TapeKind.RELEASE, AS_OF, role=Role.ANALYST)
        canary_header = summit_tape(ROWS, header=(VALID_VINS[0], *BORROWERS, *LOAN_NUMBERS))
        with pytest.raises(TapeHeaderInvalidError):
            harness.ingest(canary_header, FACILITY, TapeKind.PLEDGE, AS_OF)
        unterminated = tape + f'\r\n"{VALID_VINS[1]},{BORROWERS[1]}'.encode()
        with pytest.raises(TapeMalformedError):
            harness.ingest(unterminated, FACILITY, TapeKind.PLEDGE, AS_OF)

    outage, clock = bank(), ManualClock()
    small_outside.down = True
    try:
        limits = {"PIPELINE_MAX_ATTEMPTS": "2"}
        with BankAgentHarness(outage, small_outside, clock=clock, environment=limits) as harness:
            harness.ingest(tape, FACILITY, TapeKind.PLEDGE, AS_OF)
            for _ in range(2):
                harness.run_worker()
                clock.advance(60)
    finally:
        small_outside.down = False

    for handler in (echoing, exploding):
        with BankAgentHarness(bank(), ScriptedOutsideRecords(handler)) as harness:
            harness.ingest(tape, FACILITY, TapeKind.PLEDGE, AS_OF)
            assert [run.status.value for run in harness.run_worker().runs] == ["CLEANED", "FAILED"]

    records = records_of(captured)
    events = {record["event"] for record in records}
    expected = {
        LogEvent.TAPE_RECEIVED, LogEvent.TAPE_STAGE_ADVANCED, LogEvent.TAPE_UPLOAD_REJECTED,
        LogEvent.ACCESS_DENIED, LogEvent.TAPE_STAGE_ATTEMPT_FAILED, LogEvent.TAPE_FAILED,
        LogEvent.STAGE_UNEXPECTED_ERROR, LogEvent.CHECK_CHUNK_COMMITTED, LogEvent.DEPENDENCY_RETRY,
        LogEvent.DEPENDENCY_UNAVAILABLE, LogEvent.LIBRARY_LOG,
    }  # fmt: skip
    assert {event.value for event in expected} <= events, sorted(map(str, events))
    assert "http_request" in events, "the outside records service logged its requests"
    assert any(record.get("exception_type") == "RuntimeError" for record in records)
    assert hits(records) == []


def test_the_canary_check_itself_finds_every_form_of_a_planted_value() -> None:
    planted: list[dict[str, object]] = [
        {"ts": "2024-04-27", "event": "x", "status": VALID_VINS[0].lower()},
        {"event": "x", "count": AMOUNTS_CENTS[0]},
        {"event": "x", "stage": "27,913.46"},
        {"event": "x", "stage": "20240427"},
        {"event": "x", "route": "/v1/titles/AAAAAAAAAAAAAAAAA"},
        {"event": "x", "route": "/v1/payments?vin=1"},
    ]
    found = [token for token, _ in hits(planted)]
    assert found == [
        VALID_VINS[0],
        "2791346",
        "27,913.46",
        "20240427",
        "outside records URL",
        "outside records URL",
    ]
    assert hits([{"ts": "2024-04-27T00:00:00Z", "event": "tape_received", "count": 3}]) == []
