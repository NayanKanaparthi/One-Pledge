"""Text the bank cannot store, in anything a shared service sends (SPEC_DECISIONS 74, 75, 85).

PostgreSQL refuses U+0000 in `text` and `jsonb`, and no UTF-8 text can carry a UTF-16
surrogate code point (U+D800 to U+DFFF), which JSON can spell as an escape. Both are valid
JSON, so a registry, key holder or outside records answer can carry them. Before this rule,
a CHECKPOINT body or a quarantined payload holding U+0000 was refused by the database while
being stored, and a lone surrogate anywhere in an outbox page made the whole page malformed:
either way the message was never acknowledged and blocked the outbox for good.

Now an answer holding such text is outside the contract (MALFORMED_RESPONSE), and an outbox
message holding it is poison (VALUE_OUT_OF_RANGE, decision 100) whose payload is kept as its SHA-256 alone,
so it can be quarantined, audited and acknowledged like any other.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date

import httpx
import pytest

from bank_testkit.memory.clocks import CountingIds
from bank_testkit.scripted_http import caller, registry
from onepledge_bank_agent.domain.dependency_errors import KeyHolderRejectedError, RegistryRejectedError
from onepledge_bank_agent.domain.enums import FailureReason, TapeKind
from onepledge_bank_agent.domain.errors import OutsideRecordsRejectedError
from onepledge_bank_agent.domain.registry_records import (
    AlertNotice,
    OutboxMessage,
    QuarantinedPayload,
    RegistryItem,
    RegistrySubmission,
)
from onepledge_bank_agent.domain.storable import storable_json
from onepledge_bank_agent.domain.vin import Vin
from onepledge_bank_agent.infrastructure.http.key_holder_client import KeyHolderHttpClient
from onepledge_bank_agent.infrastructure.http.outside_records import OutsideRecordsClient

pytestmark = pytest.mark.unit

NUL = "\x00"
LONE_SURROGATE = "\ud800"
UNSTORABLE = [("U+0000", NUL), ("a lone surrogate", LONE_SURROGATE)]
ALERT = {"case_id": str(uuid.uuid4()), "role": "HOLDER", "asset_token": "d" * 64, "match_kind": "EXACT",
         "match_basis": "ASSET", "kinds_matched": ["ASSET"], "opened_seq": 4}  # fmt: skip
BODY = {"schema_version": 1, "checkpoint_seq": 3, "last_event_seq": 9000, "head_hash": "a" * 64,
        "signed_at": "2026-11-22T00:00:00Z"}  # fmt: skip
SIGNED = {"body": BODY, "signature": "A" * 86 + "==", "signing_key_id": "registry-local-1"}
VIN = Vin.parse("1HGCM82633A004352")


def wire(document: object) -> bytes:
    """The document as a service sends it: JSON with every non-ASCII character escaped, so a lone
    surrogate travels as the six characters of its escape, as it can on the wire."""
    return json.dumps(document).encode("ascii")


def message(message_type: str, payload: object) -> dict[str, object]:
    return {"message_id": str(uuid.uuid4()), "position": 1, "type": message_type,
            "created_at": "2026-11-20T12:00:00Z", "payload": payload}  # fmt: skip


def outbox_of(*messages: object) -> list[OutboxMessage]:
    content = wire({"messages": list(messages), "has_more": False})
    return list(registry(lambda r: httpx.Response(200, content=content)).outbox(100).messages)


@pytest.mark.parametrize(
    ("value", "storable"),
    [
        ({"a": ["b", {"c": "d"}], "n": 1, "t": True, "z": None}, True),
        ({"a": "caf\u00e9 \u2028\U0001f600", "n": 2**63 - 1, "m": -(2**63)}, True),
        ({"a": "x" * 255}, True),
        ({"a": NUL}, False),
        ({"a": ["ok", {"b": "x" + NUL}]}, False),
        ({"k" + NUL: 1}, False),
        ({"a": LONE_SURROGATE}, False),
        ({"a": "\udfff"}, False),
        (["x", LONE_SURROGATE + "y"], False),
        ({"a": "tab\there"}, False),
        ({"a": "\x7f"}, False),
        ({"a": "\x85"}, False),
        ({"a": "x" * 256}, False),
        ({"n": 2**63}, False),
        ({"n": -(2**63) - 1}, False),
        ({"f": 1.5}, False),
    ],
)
def test_storable_json_holds_the_text_and_integer_rules_of_decision_100(
    value: object, storable: bool
) -> None:
    assert storable_json(value) is storable


def submission_answer(request: httpx.Request, **changes: object) -> httpx.Response:
    result = {"item_ref": str(uuid.UUID(int=7)), "asset_token": "a" * 64, "result": "LOCKED",
              "lock_id": str(uuid.uuid4()), "already_held": False, "match_kind": None, "match_basis": None,
              "kinds_matched": [], "case_id": None, "reason": None}  # fmt: skip
    body = {"submission_id": str(uuid.uuid4()), "member_id": str(uuid.uuid4()), "kind": "PLEDGE",
            "key_version": "1.1", "status": "COMPLETED", "received_at": "2026-11-20T12:00:00Z",
            "as_of_seq": 9, "certificate_id": str(uuid.uuid4()), "results": [result]}  # fmt: skip
    return httpx.Response(201, content=wire({**body, **changes}))


SUBMISSION = RegistrySubmission(TapeKind.PLEDGE, "1.1", (RegistryItem(uuid.UUID(int=7), "a" * 64, None),))


@pytest.mark.parametrize(("name", "char"), UNSTORABLE)
def test_a_registry_answer_holding_unstorable_text_is_malformed(name: str, char: str) -> None:
    """The submission answer is stored as `response_body`; an extra field is enough."""
    assert registry(submission_answer).submit("f" * 64, SUBMISSION, lambda: None).status == 201
    with pytest.raises(RegistryRejectedError) as raised:
        registry(lambda r: submission_answer(r, note="x" + char)).submit("f" * 64, SUBMISSION, lambda: None)
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE, name


@pytest.mark.parametrize(("name", "char"), UNSTORABLE)
def test_a_key_holder_answer_holding_unstorable_text_is_malformed(name: str, char: str) -> None:
    point = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA="

    def answer(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        body = {"request_id": sent["request_id"], "holder_id": "kh1", "key_version": 1,
                "elements": [point] * len(sent["elements"]), "note": char}  # fmt: skip
        return httpx.Response(200, content=wire(body))

    client = KeyHolderHttpClient(caller(answer, "key_holder_1", []), "key_holder_1", CountingIds())
    with pytest.raises(KeyHolderRejectedError) as raised:
        client.evaluate([point])
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE, name


@pytest.mark.parametrize(("name", "char"), UNSTORABLE)
def test_an_outside_records_title_holding_unstorable_text_is_malformed(name: str, char: str) -> None:
    """The lienholder on the title is stored in the check's evidence."""
    title = {"vin": VIN.value, "state": "OH", "title_status": "CLEAN", "lienholder_name": "Harbor" + char,
             "lien_recorded_on": "2026-01-02"}  # fmt: skip
    client = OutsideRecordsClient(
        caller(lambda r: httpx.Response(200, content=wire(title)), "outside_records", [])
    )
    with pytest.raises(OutsideRecordsRejectedError) as raised:
        client.title(VIN)
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE, name


def test_an_outside_records_answer_is_read_with_the_bounds_of_every_other_answer() -> None:
    """Decision 74: a key twice, a float or a document nested too deeply is malformed too."""
    contract = {"contract_id": str(uuid.uuid4()), "vin": VIN.value, "lender_name": "Harbor Bank",
                "amount_cents": 41000, "signed_on": "2026-01-02"}  # fmt: skip
    good = json.dumps({"vin": VIN.value, "contracts": [contract]})
    answers = [
        good.replace('"vin"', '"vin": "1HGCM82633A004352", "vin"', 1),
        good.replace("41000", "41000.0"),
        '{"vin": "1HGCM82633A004352", "x": ' + "[" * 40 + "]" * 40 + ', "contracts": []}',
    ]

    def answering(text: str) -> OutsideRecordsClient:
        return OutsideRecordsClient(
            caller(lambda r: httpx.Response(200, content=text.encode()), "outside_records", [])
        )

    assert len(answering(good).contracts(VIN)) == 1
    for text in answers:
        with pytest.raises(OutsideRecordsRejectedError) as raised:
            answering(text).contracts(VIN)
        assert raised.value.reason is FailureReason.MALFORMED_RESPONSE


@pytest.mark.parametrize(("name", "char"), UNSTORABLE)
@pytest.mark.parametrize(
    "where",
    ["an alert's extra field", "an alert's key", "a checkpoint body's extra field", "a valid alert's value"],
)
def test_an_outbox_message_holding_unstorable_text_is_poison_kept_as_its_hash_alone(
    where: str, name: str, char: str
) -> None:
    """Quarantined as VALUE_OUT_OF_RANGE (SPEC_DECISIONS 100) by its id; the next message is applied."""
    payload: dict[str, object]
    if where == "an alert's extra field":
        payload = {**ALERT, "note": "x" + char}
    elif where == "an alert's key":
        payload = {**ALERT, "note" + char: 1}
    elif where == "a checkpoint body's extra field":
        payload = {**SIGNED, "body": {**BODY, "note": char}}
    else:
        payload = {**ALERT, "role": "HOLDER" + char}
    kind = "CHECKPOINT" if "checkpoint" in where else "ALERT"
    poison, good = message(kind, payload), message("ALERT", ALERT)
    first, second = outbox_of(poison, good)
    assert first.message_id == uuid.UUID(str(poison["message_id"]))
    assert isinstance(first.payload, QuarantinedPayload)
    assert first.payload.reason.value == "VALUE_OUT_OF_RANGE"
    assert first.payload.withheld and first.payload.payload is None
    escaped = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    assert first.payload.payload_sha256 == hashlib.sha256(escaped).hexdigest()
    assert isinstance(second.payload, AlertNotice)


def test_a_payload_that_can_be_stored_is_kept_and_hashed_in_its_canonical_utf8_form() -> None:
    payload = {**ALERT, "opened_seq": 0, "note": "café"}
    (received,) = outbox_of(message("ALERT", payload))
    assert isinstance(received.payload, QuarantinedPayload) and not received.payload.withheld
    assert received.payload.payload == payload
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert received.payload.payload_sha256 == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(("name", "char"), UNSTORABLE)
def test_a_message_of_an_unknown_type_holding_unstorable_text_is_still_ignored(name: str, char: str) -> None:
    (received,) = outbox_of(message("NEWS", {"text": char}))
    assert received.payload is None and received.message_type == "NEWS", name


@pytest.mark.parametrize(("name", "char"), UNSTORABLE)
def test_a_message_whose_type_holds_unstorable_text_has_no_usable_type(name: str, char: str) -> None:
    """Like a message without a type: the page is malformed, a named dependency error, never unexpected."""
    with pytest.raises(RegistryRejectedError) as raised:
        outbox_of(message("ALE" + char + "RT", ALERT))
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE, name


def test_the_window_of_outside_records_is_unchanged_for_a_storable_answer() -> None:
    payment = {"payment_id": str(uuid.uuid4()), "vin": VIN.value, "paid_on": "2026-10-01",
               "amount_cents": 41000, "deposit_bank_code": "HARBOR"}  # fmt: skip
    client = OutsideRecordsClient(
        caller(lambda r: httpx.Response(200, content=wire({"items": [payment], "total": 1})),
               "outside_records", [])
    )  # fmt: skip
    window = client.payments(VIN, date(2026, 9, 1), date(2026, 11, 1))
    assert len(window.payments) == 1
