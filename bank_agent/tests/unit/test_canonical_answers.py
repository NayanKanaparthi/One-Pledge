"""Canonical identifiers and encodings in everything the bank reads (SPEC_DECISIONS 74, 75; K18).

A UUID is accepted only in its one canonical spelling and a base64 value only when it
re-encodes to the same text, in every registry, key holder and outside records answer,
in every outbox message (a message that breaks the rule is poison, SCHEMA_MISMATCH, and
still acknowledged by its id) and in the trust anchors of the settings. No document the
bank reads can nest deeply enough to exhaust the parser, or give a key twice.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import date

import httpx
import pytest

from bank_testkit.memory.clocks import CountingIds
from bank_testkit.scripted_http import Handler, caller, registry
from onepledge_bank_agent.domain.dependency_errors import (
    KeyHolderProtocolError,
    KeyHolderRejectedError,
    RegistryProtocolError,
    RegistryRejectedError,
)
from onepledge_bank_agent.domain.enums import FailureReason, TapeKind
from onepledge_bank_agent.domain.errors import OutsideRecordsProtocolError
from onepledge_bank_agent.domain.registry_records import (
    OutboxMessage,
    QuarantinedPayload,
    RegistryItem,
    RegistrySubmission,
)
from onepledge_bank_agent.domain.vin import Vin
from onepledge_bank_agent.infrastructure import bounded_json
from onepledge_bank_agent.infrastructure.crypto.blinder import decode_point
from onepledge_bank_agent.infrastructure.crypto.signatures import Ed25519SignatureVerifier
from onepledge_bank_agent.infrastructure.encodings import canonical_base64, canonical_uuid
from onepledge_bank_agent.infrastructure.http.key_holder_client import KeyHolderHttpClient
from onepledge_bank_agent.infrastructure.http.outside_records import OutsideRecordsClient
from onepledge_bank_agent.infrastructure.operator_errors import OperatorError
from onepledge_bank_agent.infrastructure.settings import RuntimeSettings

pytestmark = pytest.mark.unit

IDENTITY = {"BANK_CODE": "HARBOR", "BANK_NAME": "Harbor Bank"}
SUBMISSION = RegistrySubmission(TapeKind.PLEDGE, "1.1", (RegistryItem(uuid.UUID(int=7), "a" * 64, None),))
ALERT = {"case_id": str(uuid.uuid4()), "role": "HOLDER", "asset_token": "d" * 64, "match_kind": "EXACT",
         "match_basis": "ASSET", "kinds_matched": ["ASSET"], "opened_seq": 4}  # fmt: skip
BODY = {"schema_version": 1, "checkpoint_seq": 3, "last_event_seq": 9000, "head_hash": "a" * 64,
        "signed_at": "2026-11-22T00:00:00Z"}  # fmt: skip
SIGNED = {"body": BODY, "signature": "A" * 86 + "==", "signing_key_id": "registry-local-1"}


def message(message_type: str, payload: object) -> dict[str, object]:
    """One outbox message of the contract's envelope."""
    return {"message_id": str(uuid.uuid4()), "position": 1, "type": message_type,
            "created_at": "2026-11-20T12:00:00Z", "payload": payload}  # fmt: skip


def decided(request: httpx.Request, **changes: object) -> httpx.Response:
    """A 201 `SubmissionResult` for SUBMISSION, with `changes` applied."""
    result = {"item_ref": str(uuid.UUID(int=7)), "asset_token": "a" * 64, "result": "LOCKED",
              "lock_id": str(uuid.uuid4()), "already_held": False, "match_kind": None, "match_basis": None,
              "kinds_matched": [], "case_id": None, "reason": None}  # fmt: skip
    body = {"submission_id": str(uuid.uuid4()), "member_id": str(uuid.uuid4()), "kind": "PLEDGE",
            "key_version": "1.1", "status": "COMPLETED", "received_at": "2026-11-20T12:00:00Z",
            "as_of_seq": 9, "certificate_id": str(uuid.uuid4()), "results": [result]}  # fmt: skip
    return httpx.Response(201, json={**body, **changes})


ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
SPELLINGS = [ID.upper(), ID.replace("-", ""), "{" + ID + "}", "urn:uuid:" + ID, " " + ID, ID + "\n"]
POINT = base64.b64encode(bytes(range(1, 33))).decode()
LOOSE_POINT = POINT[:42] + chr(ord(POINT[42]) + 1) + "="
"""The same 32 bytes as POINT with a padding bit set: it decodes, but does not re-encode to itself."""
SIGNATURE = SIGNED["signature"]
LOOSE_SIGNATURE = str(SIGNATURE)[:85] + "B=="


def test_only_the_canonical_spelling_of_a_uuid_is_accepted() -> None:
    assert canonical_uuid(ID) == uuid.UUID(ID)
    assert [canonical_uuid(text) for text in SPELLINGS] == [None] * len(SPELLINGS)


def test_only_base64_that_re_encodes_to_itself_is_accepted() -> None:
    assert base64.b64decode(LOOSE_POINT) == base64.b64decode(POINT), "both spellings decode alike"
    assert canonical_base64(POINT) == bytes(range(1, 33))
    assert [canonical_base64(t) for t in (LOOSE_POINT, POINT[:-1], POINT + "=", "AA==\n")] == [None] * 4
    assert decode_point(LOOSE_POINT) is None and decode_point(POINT) is not None


@pytest.mark.parametrize("spelling", SPELLINGS[:4])
def test_a_registry_answer_with_an_id_in_another_spelling_is_malformed(spelling: str) -> None:
    with pytest.raises(RegistryRejectedError) as raised:
        registry(lambda r: decided(r, certificate_id=spelling)).submit("f" * 64, SUBMISSION, lambda: None)
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE


def test_a_certificate_with_a_signature_whose_padding_bits_are_set_is_malformed() -> None:
    body = {
        "schema_version": 1, "certificate_id": ID, "member_id": ID, "submission_id": ID, "kind": "PLEDGE",
        "key_version": "1.1", "as_of_seq": 3, "issued_at": "2026-11-20T12:00:00Z",
        "summary": {"checked": 1, "locked": 1, "held": 0, "released": 0, "rejected": 0},
        "items": [{"asset_token": "a" * 64, "status": "LOCKED"}],
    }  # fmt: skip
    signed = {"body": body, "body_hash": "b" * 64, "signing_key_id": "registry-local-1"}
    assert registry(lambda r: httpx.Response(200, json={**signed, "signature": SIGNATURE})).certificate(
        uuid.UUID(ID)
    )
    with pytest.raises(RegistryRejectedError) as raised:
        registry(lambda r: httpx.Response(200, json={**signed, "signature": LOOSE_SIGNATURE})).certificate(
            uuid.UUID(ID)
        )
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE


def outbox_of(*messages: object, raw: str | None = None) -> list[OutboxMessage]:
    """The messages of one outbox page the scripted registry serves."""
    content = raw if raw is not None else json.dumps({"messages": list(messages), "has_more": False})
    page = registry(lambda r: httpx.Response(200, content=content.encode())).outbox(100)
    return list(page.messages)


POISON = [
    ("an alert whose case id is upper case", message("ALERT", {**ALERT, "case_id": ID.upper()})),
    ("an alert whose case id has braces", message("ALERT", {**ALERT, "case_id": "{" + ID + "}"})),
    ("a case update whose case id has no hyphens", message("CASE_UPDATE", {
        "case_id": ID.replace("-", ""), "asset_token": "d" * 64, "status": "CONFIRMED", "resolved_seq": 9,
    })),
    ("a checkpoint whose signature has padding bits set",
     message("CHECKPOINT", {**SIGNED, "signature": LOOSE_SIGNATURE})),
    ("an alert whose message id is upper case", {**message("ALERT", ALERT), "message_id": ID.upper()}),
    ("a message of an unknown type whose id is a urn",
     {**message("NEWS", {}), "message_id": "urn:uuid:" + ID}),
]  # fmt: skip


@pytest.mark.parametrize("case", POISON, ids=[c[0] for c in POISON])
def test_a_message_with_a_non_canonical_id_or_encoding_is_poison_and_keeps_its_id(
    case: tuple[str, dict[str, object]],
) -> None:
    """Quarantined as SCHEMA_MISMATCH (SPEC_DECISIONS 85 rule 1 with 75), so the sync acknowledges it by
    the id it carries and goes on; the page is never refused for it."""
    _, sent = case
    (received,) = outbox_of(sent)
    assert isinstance(received.payload, QuarantinedPayload)
    assert received.payload.reason.value == "SCHEMA_MISMATCH"
    assert received.message_id == uuid.UUID(str(sent["message_id"]))


def test_a_message_that_gives_a_key_twice_is_poison_and_the_rest_of_the_page_is_applied() -> None:
    good, twice = message("ALERT", ALERT), message("ALERT", ALERT)
    text = json.dumps({"messages": [twice, good], "has_more": False})
    duplicated = text.replace('"role": "HOLDER"', '"role": "CLAIMANT", "role": "HOLDER"', 1)
    first, second = outbox_of(raw=duplicated)
    assert isinstance(first.payload, QuarantinedPayload) and first.payload.reason.value == "SCHEMA_MISMATCH"
    assert not isinstance(second.payload, QuarantinedPayload)


@pytest.mark.parametrize(
    "raw",
    [
        '{"messages": [], "messages": [], "has_more": false}',
        '{"messages": [' + "[" * 40 + "]" * 40 + '], "has_more": false}',
        '{"messages": [' + "[" * 100_000 + "]" * 100_000 + '], "has_more": false}',
        '{"messages": [], "has_more": false, "x": NaN}',
    ],
    ids=["a page key given twice", "a page 42 levels deep", "a page 100,000 levels deep", "NaN"],
)
def test_a_page_that_is_not_one_bounded_document_is_malformed_never_an_unexpected_error(raw: str) -> None:
    with pytest.raises(RegistryProtocolError) as raised:
        outbox_of(raw=raw)
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE


def test_an_answer_nested_too_deeply_or_with_a_key_twice_is_malformed() -> None:
    deep = b'{"x": ' + b"[" * 100_000 + b"]" * 100_000 + b"}"
    with pytest.raises(RegistryRejectedError) as raised:
        registry(lambda r: httpx.Response(200, content=deep)).certificate(uuid.UUID(ID))
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE
    twice = json.dumps(decided(httpx.Request("POST", "http://registry.test")).json())
    twice = twice.replace('"kind": "PLEDGE"', '"kind": "RELEASE", "kind": "PLEDGE"', 1)
    with pytest.raises(RegistryRejectedError):
        registry(lambda r: httpx.Response(201, content=twice.encode())).submit(
            "f" * 64, SUBMISSION, lambda: None
        )
    assert bounded_json.holds_duplicate_key(
        bounded_json.loads('{"a": [{"b": 1, "b": 2}]}', mark_duplicates=True)
    )


def holder_answer(request: httpx.Request, **changes: object) -> httpx.Response:
    sent = json.loads(request.content)
    body = {"request_id": sent["request_id"], "holder_id": "kh1", "key_version": 1,
            "elements": [POINT] * len(sent["elements"])}  # fmt: skip
    return httpx.Response(200, json={**body, **changes})


def test_a_key_holder_answer_needs_canonical_elements_and_its_request_id_spelled_canonically() -> None:
    def client(handler: Handler) -> KeyHolderHttpClient:
        return KeyHolderHttpClient(caller(handler, "key_holder_1", []), "key_holder_1", CountingIds())

    assert client(holder_answer).evaluate([POINT]).elements == (POINT,)
    with pytest.raises(KeyHolderProtocolError):
        client(lambda r: holder_answer(r, elements=[LOOSE_POINT])).evaluate([POINT])

    def braced(request: httpx.Request) -> httpx.Response:
        return holder_answer(request, request_id="{" + json.loads(request.content)["request_id"] + "}")

    with pytest.raises(KeyHolderRejectedError) as raised:
        client(braced).evaluate([POINT])
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE


def test_an_outside_records_payment_with_an_id_in_another_spelling_is_malformed() -> None:
    vin = Vin.parse("1HGCM82633A004352")
    payment = {"payment_id": ID.upper(), "vin": vin.value, "paid_on": "2026-10-01",
               "amount_cents": 41000, "deposit_bank_code": "HARBOR"}  # fmt: skip

    def page(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [payment], "total": 1})

    client = OutsideRecordsClient(caller(page, "outside_records", []))
    with pytest.raises(OutsideRecordsProtocolError):
        client.payments(vin, date(2026, 9, 1), date(2026, 11, 1))


def test_a_trust_anchor_in_a_non_canonical_base64_spelling_is_refused() -> None:
    key = base64.b64encode(bytes(range(32))).decode()
    loose = key[:42] + chr(ord(key[42]) + 1) + "="
    assert base64.b64decode(loose) == base64.b64decode(key)
    with pytest.raises(OperatorError) as raised:
        RuntimeSettings.from_env(
            {**IDENTITY, "REGISTRY_PUBLIC_KEYS": json.dumps({"registry-local-1": loose})}
        )
    assert raised.value.sentence.startswith("Configuration error: REGISTRY_PUBLIC_KEYS must be")
    verifier = Ed25519SignatureVerifier({"registry-local-1": key})
    assert not verifier.verifies("registry-local-1", b"m", LOOSE_SIGNATURE)


@pytest.mark.parametrize("literal", ["1" * 31, "-" + "1" * 30, "1." + "0" * 29])
def test_a_number_literal_longer_than_30_characters_is_not_json(literal: str) -> None:
    """Decisions 38 and 74: the bounded parser refuses a number literal of more than 30 characters."""
    assert bounded_json.loads('{"n": ' + "9" * 30 + "}") == {"n": int("9" * 30)}
    with pytest.raises(bounded_json.JsonRefusedError) as raised:
        bounded_json.loads('{"n": ' + literal + "}")
    assert raised.value.problem is bounded_json.JsonProblem.NOT_JSON


def test_an_answer_with_a_number_literal_longer_than_30_characters_is_malformed() -> None:
    long_seq = json.dumps(decided(httpx.Request("POST", "http://registry.test")).json())
    long_seq = long_seq.replace('"as_of_seq": 9', '"as_of_seq": ' + "1" * 31, 1)
    with pytest.raises(RegistryRejectedError) as raised:
        registry(lambda r: httpx.Response(201, content=long_seq.encode())).submit(
            "f" * 64, SUBMISSION, lambda: None
        )
    assert raised.value.reason is FailureReason.MALFORMED_RESPONSE


def test_an_outbox_number_longer_than_30_characters_is_its_messages_problem_not_the_pages() -> None:
    """Decision 85: a 31 digit `last_event_seq` is VALUE_OUT_OF_RANGE for that message alone."""
    body = {**BODY, "last_event_seq": 10**30}
    first, second = outbox_of(message("CHECKPOINT", {**SIGNED, "body": body}), message("ALERT", ALERT))
    assert isinstance(first.payload, QuarantinedPayload)
    assert first.payload.reason.value == "VALUE_OUT_OF_RANGE"
    assert not isinstance(second.payload, QuarantinedPayload)
