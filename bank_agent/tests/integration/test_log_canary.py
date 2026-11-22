"""The log canary test of SPEC 16.5 and 21.5 over the whole pipeline.

A tape with known canary values goes through every stage and through the failing
paths of intake, clean and check (rejected uploads, a duplicate, a forbidden upload,
an outage up to the attempt limit, a protocol violation, an unexpected exception
whose message carries the values). Then the whole `small` data set is replayed for
three banks and driven through the failing paths of the registry side (key holder and
registry outages up to the attempt limit, a refused key, a tampered certificate, a
forged checkpoint, deep verification). Every log record of the bank agent, the
outside records service and the doubles is captured at DEBUG. No canary value may
appear, in any case, in any field other than `ts`; no outside records URL with a VIN
may appear at all; and no canary value reaches the registry or a key holder, an
audit entry or an alert.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import uuid
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest
import structlog

from bank_testkit.banks import ThreeBanks
from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase
from bank_testkit.doubles import KEY_HOLDER_URLS, REGISTRY_URL, InProcessDoubles
from bank_testkit.memory.clocks import ManualClock
from bank_testkit.outside import InProcessOutsideRecords, ScriptedOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from bank_testkit.tapes import summit_row, summit_tape
from bank_testkit.world import Faults
from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.faults import FaultPoint
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
    doubles: InProcessDoubles,
) -> None:
    tape = summit_tape(ROWS)

    def bank() -> BankDatabase:
        database = new_database()
        seed_reference(database, small_data)
        return database

    happy = bank()
    with BankAgentHarness(happy, Services(small_outside, doubles)) as harness:
        harness.ingest(tape, FACILITY, TapeKind.PLEDGE, AS_OF)
        assert [run.status.value for run in harness.run_worker().runs] == [
            "CLEANED", "CHECKED", "TOKENIZED", "SUBMITTED", "COMPLETED",
        ]  # fmt: skip
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
        services = Services(small_outside, doubles)
        with BankAgentHarness(outage, services, clock=clock, environment=limits) as harness:
            harness.ingest(tape, FACILITY, TapeKind.PLEDGE, AS_OF)
            for _ in range(2):
                harness.run_worker()
                clock.advance(60)
    finally:
        small_outside.down = False

    for handler in (echoing, exploding):
        with BankAgentHarness(bank(), Services(ScriptedOutsideRecords(handler), doubles)) as harness:
            harness.ingest(tape, FACILITY, TapeKind.PLEDGE, AS_OF)
            assert [run.status.value for run in harness.run_worker().runs] == ["CLEANED", "FAILED"]

    library = logging.getLogger("httpx")
    library.info("dropped below WARNING %s", VALID_VINS[0])
    library.warning("a library warning that names %s and %s", VALID_VINS[1], BORROWERS[1])

    records = records_of(captured)
    events = {record["event"] for record in records}
    assert [record["level"] for record in records if record["event"] == "library_log"] == ["warning"]
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


AMOUNT_CELL = re.compile(r"^\$?(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})$")
US_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
SAMPLED_VALUES = 300


def forms(cell: str) -> list[str]:
    """A tape cell and every normalised form the bank could write it in."""
    text = cell.strip()
    found = [text]
    if amount := AMOUNT_CELL.fullmatch(text):
        cents = int(amount[1].replace(",", "")) * 100 + int(amount[2])
        found += [str(cents), f"{cents / 100:.2f}", f"{cents / 100:,.2f}"]
    for pattern, order in ((US_DATE, (3, 1, 2)), (ISO_DATE, (1, 2, 3))):
        if day := pattern.fullmatch(text):
            year, month, dom = (day[i] for i in order)
            found += [f"{year}-{month}-{dom}", f"{year}{month}{dom}", f"{month}/{dom}/{year}"]
    if re.fullmatch(r"[A-Za-z0-9 \-]{17,21}", text):
        found.append(re.sub(r"[ \-]", "", text).upper())
    return found


def tape_canaries(data: Path) -> list[str]:
    """An evenly spaced sample of the loan values of every tape (each value with at least one digit)."""
    values: set[str] = set()
    for tape in sorted((data / "tapes").rglob("*.csv")):
        records = list(csv.reader(io.StringIO(tape.read_bytes().decode("utf-8").removeprefix("﻿"))))
        for record in records[1:]:
            for cell in record[:6]:
                if len(cell.strip()) >= 6 and any(char.isdigit() for char in cell):
                    values.update(forms(cell))
    ordered = sorted(values)
    return ordered[:: max(1, len(ordered) // SAMPLED_VALUES)]


def found_in(texts: list[str], canaries: list[str]) -> list[tuple[str, str]]:
    patterns = [
        (value, re.compile(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", re.IGNORECASE))
        for value in canaries
    ]
    return [(value, text[:200]) for text in texts for value, pattern in patterns if pattern.search(text)]


def stored_texts(database: BankDatabase) -> list[str]:
    """Every audit detail and every alert explanation of one bank."""
    with database.connect("admin") as connection:
        details = [str(row[0]) for row in connection.execute("SELECT detail::text FROM audit_log")]
        alerts = [str(row[0]) for row in connection.execute("SELECT explanation_text FROM alerts")]
    return details + alerts


class TamperFirstCertificate(Faults):
    """CERTIFICATE_BODY on the first answer of one bank, before the bank fetches the certificate."""

    def __init__(self, banks: ThreeBanks, code: str) -> None:
        super().__init__()
        self._banks, self._code = banks, code

    def reached(self, point: FaultPoint) -> None:
        if point is FaultPoint.AFTER_RESPONSE_STORED and point not in self.passed:
            with self._banks.databases[self._code].connect("admin") as connection:
                row = connection.execute(
                    "SELECT certificate_id FROM registry_requests WHERE certificate_id IS NOT NULL "
                    "ORDER BY responded_at DESC LIMIT 1"
                ).fetchone()
            assert row is not None
            self._banks.doubles.tamper(mode="CERTIFICATE_BODY", certificate_id=str(row[0]))
        super().reached(point)


def locked_loan(database: BankDatabase) -> tuple[uuid.UUID, uuid.UUID]:
    with database.connect("admin") as connection:
        row = connection.execute(
            "SELECT loan_id, facility_id FROM loans WHERE state = 'LOCKED' ORDER BY loan_id LIMIT 1"
        ).fetchone()
    assert row is not None and isinstance(row[0], uuid.UUID) and isinstance(row[1], uuid.UUID)
    return row[0], row[1]


def drive_registry_failures(banks: ThreeBanks, data: Path) -> None:
    """The failing paths of the registry side, after the replay."""
    clock, limits = ManualClock(), {"PIPELINE_MAX_ATTEMPTS": "2"}
    for code, down in (("HARBOR", REGISTRY_URL), ("NORTHGATE", KEY_HOLDER_URLS["kh2"])):
        loan, facility = locked_loan(banks.databases[code])
        with banks.agent_with(code, clock=clock, environment=limits) as agent:
            banks.doubles.down.add(down)
            if down == REGISTRY_URL:
                agent.release(facility, [loan], date(2026, 11, 18))
            else:
                agent.ingest(data / "tapes/NORTHGATE/S03_NORTHGATE-SUMMIT_BACK_BOOK.csv", "NORTHGATE-SUMMIT",
                             TapeKind.PLEDGE, date(2026, 11, 18))  # fmt: skip
            for _ in range(3):
                agent.run_worker()
                clock.advance(3600)
            banks.doubles.down.clear()
    loan, facility = locked_loan(banks.databases["CALDER"])
    with banks.agent_with("CALDER", TamperFirstCertificate(banks, "CALDER")) as agent:
        agent.release(facility, [loan], date(2026, 11, 18))
        agent.run_worker()
    with banks.agent_with("HARBOR", environment={"KEY_HOLDER_1_API_KEY": "khk_" + "w" * 43}) as agent:
        agent.ingest(data / "tapes/HARBOR/S13_HARBOR-SUMMIT_PLEDGE.csv", "HARBOR-SUMMIT", TapeKind.BACK_BOOK,
                     date(2026, 11, 19))  # fmt: skip
        agent.run_worker()
    latest = banks.doubles.checkpoints()[-1]["body"]
    banks.doubles.forge_checkpoint("HARBOR", {**latest, "head_hash": "f" * 64})
    harbor = banks.agents["HARBOR"]
    harbor.sync()
    loan, facility = locked_loan(banks.databases["HARBOR"])
    harbor.release(facility, [loan], date(2026, 11, 20))
    harbor.run_worker()
    banks.doubles.tamper(mode="EDIT_IN_PLACE", seq=3)
    for agent in banks.agents.values():
        agent.sync()
        agent.verify()
        agent.report()


def test_no_canary_value_reaches_any_log_record_or_any_service_on_the_registry_side(
    captured: io.StringIO,
    new_database: Callable[[], BankDatabase],
    small_data: Path,
    small_outside: InProcessOutsideRecords,
) -> None:
    canaries = tape_canaries(small_data)
    assert len(canaries) >= SAMPLED_VALUES and any(len(value) == 17 and value.isalnum() for value in canaries)
    missed = [value for value in canaries if not found_in([f'{{"stage": "{value.lower()}"}}'], [value])]
    assert missed == [], "the check finds every planted value"
    with ThreeBanks(new_database, small_data, small_outside) as banks:
        banks.replay()
        drive_registry_failures(banks, small_data)
        received = [
            request["body"]
            for url in (REGISTRY_URL, *KEY_HOLDER_URLS.values())
            for request in banks.doubles.received(url)
        ]
        stored = [text for database in banks.databases.values() for text in stored_texts(database)]
    records = records_of(captured)
    events = {str(record["event"]) for record in records}
    expected = {
        LogEvent.CODES_STORED, LogEvent.REGISTRY_REQUESTS_PLANNED, LogEvent.REGISTRY_REQUEST_SENT,
        LogEvent.REGISTRY_RESPONSE_STORED, LogEvent.CERTIFICATE_STORED, LogEvent.OUTBOX_SYNCED,
        LogEvent.CHECKPOINT_STORED, LogEvent.ALERT_CREATED, LogEvent.HALT_RULE_APPLIED, LogEvent.TAPE_FAILED,
        LogEvent.TAPE_STAGE_ATTEMPT_FAILED, LogEvent.DEPENDENCY_UNAVAILABLE, LogEvent.INTEGRITY_VERIFIED,
    }  # fmt: skip
    assert {event.value for event in expected} <= events, sorted(
        expected - set(map(LogEvent, events & set(LogEvent)))
    )
    assert received and stored
    texts = [json.dumps({key: value for key, value in record.items() if key != "ts"}) for record in records]
    assert [text for text in texts if OUTSIDE_URL.search(text)] == []
    assert found_in(texts, canaries) == []
    assert found_in(received, canaries) == []
    assert found_in(stored, canaries) == []
