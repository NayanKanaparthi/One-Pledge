"""The round-trip rule: nothing is stored that every reader cannot read back (SPEC_DECISIONS 100).

Review 3 F1: a CHECKPOINT whose `signed_at` was `9999-12-31T23:59:59-12:00` or
`0001-01-01T00:00:00+12:00`, and a certificate whose `issued_at` was the latter, were stored;
PostgreSQL held the value, the driver could not read it back, and the integrity report failed
for good. Each is now refused before storage: the message is poison VALUE_OUT_OF_RANGE, the
answer MALFORMED_RESPONSE. The value objects and the wire models share one range.
"""

from __future__ import annotations

import ast
import json
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from bank_testkit.memory.clocks import CountingIds
from bank_testkit.scripted_http import caller, messages, registry
from onepledge_bank_agent.domain.dependency_errors import KeyHolderRejectedError, RegistryRejectedError
from onepledge_bank_agent.domain.enums import FailureReason
from onepledge_bank_agent.domain.errors import InvalidValueError, OutsideRecordsRejectedError
from onepledge_bank_agent.domain.registry_records import QuarantinedPayload, SignedCheckpoint
from onepledge_bank_agent.domain.storable import (
    EARLIEST_INSTANT,
    END_INSTANT,
    StorableDate,
    StorableTimestamp,
    storable_texts,
)
from onepledge_bank_agent.domain.vin import Vin
from onepledge_bank_agent.infrastructure.http.key_holder_client import KeyHolderHttpClient
from onepledge_bank_agent.infrastructure.http.outside_records import OutsideRecordsClient

pytestmark = pytest.mark.unit

FAR_FUTURE = "9999-12-31T23:59:59-12:00"
FAR_PAST = "0001-01-01T00:00:00+12:00"
BODY = {"schema_version": 1, "checkpoint_seq": 3, "last_event_seq": 9000, "head_hash": "a" * 64,
        "signed_at": "2026-11-22T00:00:00Z"}  # fmt: skip
SIGNED = {"body": BODY, "signature": "A" * 86 + "==", "signing_key_id": "registry-local-1"}
ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
VIN = Vin.parse("1HGCM82633A004352")
REPOSITORIES = Path(__file__).resolve().parents[2] / "src/onepledge_bank_agent/infrastructure/db/repositories"


def message(signed_at: str) -> dict[str, object]:
    payload = {**SIGNED, "body": {**BODY, "signed_at": signed_at}}
    return {"message_id": str(uuid.uuid4()), "position": 1, "type": "CHECKPOINT",
            "created_at": "2026-11-20T12:00:00Z", "payload": payload}  # fmt: skip


def received(signed_at: str) -> object:
    content = json.dumps({"messages": [message(signed_at)], "has_more": False}).encode()
    (one,) = messages(registry(lambda r: httpx.Response(200, content=content)).outbox(100))
    return one.payload


@pytest.mark.parametrize(
    "signed_at",
    [
        FAR_FUTURE,
        FAR_PAST,
        "2100-01-01T00:00:00Z",
        "2000-01-01T00:00:00+00:01",
        "1999-12-31T23:59:59.999999Z",
    ],
)
def test_a_checkpoint_signed_outside_the_storable_range_is_poison_value_out_of_range(signed_at: str) -> None:
    payload = received(signed_at)
    assert isinstance(payload, QuarantinedPayload)
    assert payload.reason.value == "VALUE_OUT_OF_RANGE"


@pytest.mark.parametrize(
    "signed_at", ["2099-12-31T23:59:59.999999Z", "2000-01-01T00:00:00Z", "2100-01-01T05:00:00+05:01"]
)
def test_a_checkpoint_signed_at_the_edges_inside_the_range_is_read(signed_at: str) -> None:
    payload = received(signed_at)
    assert isinstance(payload, SignedCheckpoint)
    assert EARLIEST_INSTANT <= payload.signed_at.value < END_INSTANT


@pytest.mark.parametrize(
    "signed_at",
    ["2026-11-22T00:00:00.1234567Z", "2026-11-22T00:00:00", "2026-11-22 00:00:00Z", "2026-13-01T00:00:00Z"],
)
def test_a_timestamp_not_in_rfc_3339_with_an_offset_is_a_schema_mismatch(signed_at: str) -> None:
    payload = received(signed_at)
    assert isinstance(payload, QuarantinedPayload)
    assert payload.reason.value == "SCHEMA_MISMATCH"


def test_a_certificate_issued_in_the_year_one_is_a_malformed_answer() -> None:
    body = {
        "schema_version": 1, "certificate_id": ID, "member_id": ID, "submission_id": ID, "kind": "PLEDGE",
        "key_version": "1.1", "as_of_seq": 3, "issued_at": FAR_PAST,
        "summary": {"checked": 1, "locked": 1, "held": 0, "released": 0, "rejected": 0},
        "items": [{"asset_token": "a" * 64, "status": "LOCKED"}],
    }  # fmt: skip
    signed = {"body": body, "body_hash": "b" * 64, "signature": "A" * 86 + "==", "signing_key_id": "k-1"}
    with pytest.raises(RegistryRejectedError) as raised:
        registry(lambda r: httpx.Response(200, json=signed)).certificate(uuid.UUID(ID))
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE


@pytest.mark.parametrize("field", ["paid_on", "amount_cents"])
def test_an_outside_records_payment_outside_the_storable_range_is_malformed(field: str) -> None:
    payment = {"payment_id": str(uuid.uuid4()), "vin": VIN.value, "paid_on": "2026-10-01",
               "amount_cents": 41000, "deposit_bank_code": "HARBOR"}  # fmt: skip
    payment[field] = "1999-12-31" if field == "paid_on" else 2**63
    client = OutsideRecordsClient(
        caller(lambda r: httpx.Response(200, json={"items": [payment], "total": 1}), "outside_records", [])
    )
    with pytest.raises(OutsideRecordsRejectedError):
        client.payments(VIN, date(2026, 9, 1), date(2026, 11, 1))


def test_a_key_version_that_does_not_fit_an_int_column_is_malformed() -> None:
    point = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA="

    def answer(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        return httpx.Response(200, json={"request_id": sent["request_id"], "holder_id": "kh1",
                                         "key_version": 2**31, "elements": [point]})  # fmt: skip

    client = KeyHolderHttpClient(caller(answer, "key_holder_1", []), "key_holder_1", CountingIds())
    with pytest.raises(KeyHolderRejectedError) as raised:
        client.evaluate([point])
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE


@pytest.mark.parametrize(
    "value",
    [
        datetime(1999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
        datetime(2100, 1, 1, tzinfo=UTC),
        datetime(2000, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=1))),
        datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=-12))),
        datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=12))),
        datetime(2026, 1, 1),
    ],
    ids=["just before", "the end itself", "an offset that pushes it out", "year 10000", "year 0", "naive"],
)
def test_a_timestamp_outside_the_range_or_without_an_offset_is_refused(value: datetime) -> None:
    with pytest.raises(InvalidValueError):
        StorableTimestamp(value)


@given(st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2099, 12, 31, 23, 59, 59, 999999)))
def test_every_instant_inside_the_range_is_kept_as_the_same_instant_in_utc(moment: datetime) -> None:
    stored = StorableTimestamp(moment.replace(tzinfo=UTC))
    assert stored.value == moment.replace(tzinfo=UTC) and stored.value.tzinfo is UTC


@pytest.mark.parametrize("day", [date(1999, 12, 31), date(2100, 1, 1)])
def test_a_date_outside_2000_to_2099_is_refused(day: date) -> None:
    with pytest.raises(InvalidValueError):
        StorableDate(day)
    assert StorableDate(date(2000, 1, 1)).value == date(2000, 1, 1)


def test_no_repository_takes_a_wire_model_or_a_wire_module() -> None:
    """Repositories accept domain types only (decision 100, layer 1): none imports the HTTP layer."""
    found = []
    for module in sorted(REPOSITORIES.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "onepledge_bank_agent.infrastructure.http"
            ):
                found.append(f"{module.name}: {node.module}")
    assert found == []


def test_storable_texts_looks_at_every_key_and_string_and_at_no_number() -> None:
    assert storable_texts({"a": [1.5, 2**80, {"b": "text"}]})
    assert not storable_texts({"a": [{"b\x00": 1}]})
    assert not storable_texts(["ok", "\ud800"])
