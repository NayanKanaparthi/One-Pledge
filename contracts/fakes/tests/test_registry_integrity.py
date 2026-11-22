"""Ledger hash chain, checkpoint and certificate signatures, and the tamper modes of the registry double."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from onepledge_contract_fakes.canonical_json import canonical_sha256
from onepledge_contract_fakes.errors import CertificateNotFoundError, ValidationFailedError
from onepledge_contract_fakes.registry.double import RegistryDouble
from onepledge_contract_fakes.registry.inspection import TamperMode, TamperRequest, parse_tamper_request
from onepledge_contract_fakes.registry.ledger import event_hash
from onepledge_contract_fakes.registry.model import FIELD_KINDS, Submission, SubmissionItem, SubmissionKind
from onepledge_contract_fakes.registry.signing import flip_one_bit, verify_signature
from onepledge_contract_fakes.testing import (
    CountingIds,
    FixedClock,
    new_api_key,
    new_members,
    registry_config,
)

pytestmark = pytest.mark.unit


def canonical(value: object) -> bytes:
    """Independent canonical JSON for the checks below."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def code(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def item(name: str) -> SubmissionItem:
    return SubmissionItem(str(uuid.uuid4()), code("asset" + name), {k: code(k + name) for k in FIELD_KINDS})


def plain(value: object) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(value))
    return result


@pytest.fixture
def registry() -> tuple[RegistryDouble, list[str]]:
    members, _ = new_members(2)
    double = RegistryDouble(registry_config(members, new_api_key()), FixedClock(), CountingIds())
    ids = [m.member_id for m in members]
    for index in range(3):
        submission = Submission(SubmissionKind.PLEDGE, "1.1", (item(f"loan-{index}"),))
        double.submit(ids[index % 2], code(f"key-{index}"), submission, code(f"body-{index}"))
    return double, ids


def events(double: RegistryDouble) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = plain(double.ledger_events(0, 1000, True, "auditor"))["events"]
    return events


def first_chain_break(chain: list[dict[str, Any]]) -> int | None:
    """Independent recomputation of SPEC 15.3 step 6 over full events."""
    previous = "0" * 64
    for event in chain:
        header = {
            k: event[k] for k in ("event_type", "payload_hash", "prev_hash", "recorded_at", "schema_version")
        }
        header["seq"] = event["seq"]
        if (
            event["prev_hash"] != previous
            or event["event_hash"] != hashlib.sha256(canonical(header)).hexdigest()
            or event["payload_hash"] != hashlib.sha256(canonical(event["payload"])).hexdigest()
        ):
            return int(event["seq"])
        previous = event["event_hash"]
    return None


def verifies(public_key_b64: str, body: object, signature_b64: str) -> bool:
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    try:
        key.verify(base64.b64decode(signature_b64), canonical(body))
    except InvalidSignature:
        return False
    return True


def test_the_ledger_is_gapless_and_chained(registry: tuple[RegistryDouble, list[str]]) -> None:
    double, _ = registry
    chain = events(double)
    assert [e["seq"] for e in chain] == list(range(1, len(chain) + 1))
    assert first_chain_break(chain) is None
    headers = plain(double.ledger_events(0, 1000, False, "member"))["events"]
    assert all("payload" not in h for h in headers)
    assert plain(double.ledger_events(2, 3, False, "member"))["events"][0]["seq"] == 3


def test_the_published_ledger_event_vector_is_reproduced(contracts_dir: Path) -> None:
    vector = json.loads((contracts_dir / "examples" / "vectors" / "signatures.json").read_text())[
        "ledger_event"
    ]
    payload_hash = canonical_sha256(vector["payload"])
    assert payload_hash == vector["payload_hash"]
    recomputed = event_hash(1, "MEMBER_JOINED", payload_hash, "0" * 64, "2026-09-30T09:00:00Z")
    assert recomputed == vector["event_hash"]


def test_every_checkpoint_signs_the_head_and_verifies(registry: tuple[RegistryDouble, list[str]]) -> None:
    double, _ = registry
    chain = events(double)
    checkpoint = plain(double.latest_checkpoint())
    body = checkpoint["body"]
    assert body["head_hash"] == chain[body["last_event_seq"] - 1]["event_hash"]
    assert verifies(double.public_key_b64, body, checkpoint["signature"])
    assert verify_signature(double.public_key_b64, body, checkpoint["signature"])
    assert not verify_signature(double.public_key_b64, body, flip_one_bit(checkpoint["signature"]))
    assert checkpoint["signing_key_id"] == double.signing_key_id


def test_certificates_hash_and_sign_the_canonical_body(registry: tuple[RegistryDouble, list[str]]) -> None:
    double, ids = registry
    submission = Submission(SubmissionKind.PLEDGE, "1.1", (item("loan-0"), item("fresh")))
    result = json.loads(double.submit(ids[1], code("k"), submission, code("b")).body)
    certificate = plain(double.certificate(ids[1], result["certificate_id"]))
    body = certificate["body"]
    assert certificate["body_hash"] == hashlib.sha256(canonical(body)).hexdigest()
    assert verifies(double.public_key_b64, body, certificate["signature"])
    assert body["as_of_seq"] == result["as_of_seq"]
    assert body["summary"] == {"checked": 2, "locked": 1, "held": 1, "released": 0, "rejected": 0}
    assert [i["status"] for i in body["items"]] == ["HELD", "LOCKED"]
    assert [i["asset_token"] for i in body["items"]] == [i.asset_token for i in submission.items]
    issued = next(e for e in events(double) if e["seq"] == result["as_of_seq"])
    assert issued["event_type"] == "CERTIFICATE_ISSUED"


def test_rewrite_history_recomputes_the_chain_and_signs_a_new_head(
    registry: tuple[RegistryDouble, list[str]],
) -> None:
    double, _ = registry
    before = events(double)
    old_checkpoint = plain(double.latest_checkpoint())["body"]
    double.test_controls.tamper(TamperRequest(TamperMode.REWRITE_HISTORY, seq=3))
    after = events(double)
    assert first_chain_break(after) is None
    assert after[2]["payload"] != before[2]["payload"]
    assert after[old_checkpoint["last_event_seq"] - 1]["event_hash"] != old_checkpoint["head_hash"]
    new_checkpoint = plain(double.latest_checkpoint())
    assert new_checkpoint["body"]["checkpoint_seq"] == old_checkpoint["checkpoint_seq"] + 1
    assert verifies(double.public_key_b64, new_checkpoint["body"], new_checkpoint["signature"])


def test_edit_in_place_breaks_the_chain_at_that_seq(registry: tuple[RegistryDouble, list[str]]) -> None:
    double, _ = registry
    double.test_controls.tamper(TamperRequest(TamperMode.EDIT_IN_PLACE, seq=3))
    assert first_chain_break(events(double)) == 3


def test_bad_checkpoint_signature_is_distributed(registry: tuple[RegistryDouble, list[str]]) -> None:
    double, ids = registry
    double.test_controls.tamper(TamperRequest(TamperMode.BAD_CHECKPOINT_SIGNATURE))
    latest = plain(double.latest_checkpoint())
    assert not verifies(double.public_key_b64, latest["body"], latest["signature"])
    messages = plain(double.outbox(ids[0], 200))["messages"]
    assert (
        messages[-1]["type"] == "CHECKPOINT" and messages[-1]["payload"]["signature"] == latest["signature"]
    )


def test_certificate_body_tamper_keeps_the_old_hash_and_signature(
    registry: tuple[RegistryDouble, list[str]],
) -> None:
    double, ids = registry
    submission = Submission(SubmissionKind.PLEDGE, "1.1", (item("t"),))
    certificate_id = json.loads(double.submit(ids[0], code("t"), submission, code("t")).body)[
        "certificate_id"
    ]
    double.test_controls.tamper(TamperRequest(TamperMode.CERTIFICATE_BODY, certificate_id=certificate_id))
    certificate = plain(double.certificate(ids[0], certificate_id))
    assert certificate["body"]["summary"]["locked"] == 2
    assert certificate["body_hash"] != hashlib.sha256(canonical(certificate["body"])).hexdigest()
    with pytest.raises(CertificateNotFoundError):
        double.test_controls.tamper(
            TamperRequest(TamperMode.CERTIFICATE_BODY, certificate_id=str(uuid.uuid4()))
        )


def test_checkpoint_now_appends_and_distributes(registry: tuple[RegistryDouble, list[str]]) -> None:
    double, ids = registry
    before = len(plain(double.outbox(ids[1], 200))["messages"])
    signed = plain(double.test_controls.checkpoint_now())
    assert signed["body"]["last_event_seq"] == events(double)[-2]["seq"]
    assert len(plain(double.outbox(ids[1], 200))["messages"]) == before + 1


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"mode": "DELETE_EVERYTHING"},
        {"mode": "REWRITE_HISTORY"},
        {"mode": "EDIT_IN_PLACE", "seq": 0},
        {"mode": "CERTIFICATE_BODY"},
        {"mode": "BAD_CHECKPOINT_SIGNATURE", "seq": 3},
        [],
    ],
)
def test_malformed_tamper_requests_are_refused(document: object) -> None:
    with pytest.raises(ValidationFailedError):
        parse_tamper_request(document)


def test_tamper_seq_beyond_the_head_is_refused(registry: tuple[RegistryDouble, list[str]]) -> None:
    double, _ = registry
    with pytest.raises(ValidationFailedError):
        double.test_controls.tamper(TamperRequest(TamperMode.EDIT_IN_PLACE, seq=10_000))


def test_dump_holds_codes_and_identifiers(registry: tuple[RegistryDouble, list[str]]) -> None:
    double, ids = registry
    dump = plain(double.test_controls.dump())
    assert dump["members"] == ids
    assert len(dump["locks"]) == 3 and all(lock["active"] for lock in dump["locks"])
    assert len(dump["events"]) == len(events(double)) and len(dump["certificates"]) == 3


# SPEC_DECISIONS 65: CERTIFICATE_RESTORE puts a certificate back to the document first signed.


def signed_certificate(double: RegistryDouble, member: str, name: str) -> str:
    submission = Submission(SubmissionKind.PLEDGE, "1.1", (item(name),))
    certificate_id: str = json.loads(double.submit(member, code(name), submission, code(name)).body)[
        "certificate_id"
    ]
    return certificate_id


def test_certificate_restore_undoes_a_body_tamper(registry: tuple[RegistryDouble, list[str]]) -> None:
    double, ids = registry
    certificate_id = signed_certificate(double, ids[0], "r")
    original = plain(double.certificate(ids[0], certificate_id))
    double.test_controls.tamper(TamperRequest(TamperMode.CERTIFICATE_BODY, certificate_id=certificate_id))
    double.test_controls.tamper(TamperRequest(TamperMode.CERTIFICATE_BODY, certificate_id=certificate_id))
    answer = double.test_controls.tamper(
        TamperRequest(TamperMode.CERTIFICATE_RESTORE, certificate_id=certificate_id)
    )
    assert answer == {"mode": "CERTIFICATE_RESTORE", "certificate_id": certificate_id, "restored": True}
    restored = plain(double.certificate(ids[0], certificate_id))
    assert restored == original
    assert restored["body_hash"] == hashlib.sha256(canonical(restored["body"])).hexdigest()
    assert canonical_sha256(restored["body"]) == original["body_hash"]


def test_certificate_restore_is_also_a_method_of_the_controls(
    registry: tuple[RegistryDouble, list[str]],
) -> None:
    double, ids = registry
    certificate_id = signed_certificate(double, ids[0], "m")
    original = plain(double.certificate(ids[0], certificate_id))
    double.test_controls.tamper(TamperRequest(TamperMode.CERTIFICATE_BODY, certificate_id=certificate_id))
    assert double.test_controls.restore_certificate(certificate_id) is True
    assert plain(double.certificate(ids[0], certificate_id)) == original


def test_restoring_an_untampered_certificate_is_a_no_op(registry: tuple[RegistryDouble, list[str]]) -> None:
    double, ids = registry
    certificate_id = signed_certificate(double, ids[0], "n")
    original = plain(double.certificate(ids[0], certificate_id))
    answer = double.test_controls.tamper(
        TamperRequest(TamperMode.CERTIFICATE_RESTORE, certificate_id=certificate_id)
    )
    assert answer["restored"] is False
    assert plain(double.certificate(ids[0], certificate_id)) == original


def test_restoring_an_unknown_certificate_is_not_found(registry: tuple[RegistryDouble, list[str]]) -> None:
    double, _ = registry
    with pytest.raises(CertificateNotFoundError):
        double.test_controls.tamper(
            TamperRequest(TamperMode.CERTIFICATE_RESTORE, certificate_id=str(uuid.uuid4()))
        )


def test_the_original_is_kept_apart_from_what_is_served(registry: tuple[RegistryDouble, list[str]]) -> None:
    """Editing a served copy changes nothing: the original is a private deep copy."""
    double, ids = registry
    certificate_id = signed_certificate(double, ids[0], "c")
    served = plain(double.certificate(ids[0], certificate_id))
    served["body"]["summary"]["locked"] = 99
    double.test_controls.tamper(TamperRequest(TamperMode.CERTIFICATE_BODY, certificate_id=certificate_id))
    double.test_controls.restore_certificate(certificate_id)
    assert plain(double.certificate(ids[0], certificate_id))["body"]["summary"]["locked"] == 1


@pytest.mark.parametrize(
    "document", [{"mode": "CERTIFICATE_RESTORE"}, {"mode": "CERTIFICATE_RESTORE", "certificate_id": 5}]
)
def test_a_restore_needs_a_certificate_id(document: object) -> None:
    with pytest.raises(ValidationFailedError):
        parse_tamper_request(document)
