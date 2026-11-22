"""Certificate and checkpoint verification rules (SPEC 15.2, 15.3, 15.6) with real Ed25519 signatures.

A key generated in the test signs bodies exactly as the registry does; the bank's own
`Ed25519SignatureVerifier` checks them. Every reason is produced from a valid proof
by one change.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from onepledge_bank_agent.domain.canonical_json import ZERO_HASH, JsonValue, canonical_json, canonical_sha256
from onepledge_bank_agent.domain.certificate_rules import (
    CertificateExpectation,
    content_matches,
    delivered_sha256,
    verify_certificate,
)
from onepledge_bank_agent.domain.checkpoint_rules import (
    GENESIS,
    ChainStart,
    check_segment,
    deep_findings,
    event_header_hash,
    first_chain_break,
    screen,
)
from onepledge_bank_agent.domain.entities import Checkpoint
from onepledge_bank_agent.domain.enums import (
    CertificateFailure,
    CheckpointFailure,
    RegistryItemResult,
    TapeKind,
)
from onepledge_bank_agent.domain.registry_records import EventHeader, SignedCertificate, SignedCheckpoint
from onepledge_bank_agent.domain.values import Sha256Hex
from onepledge_bank_agent.infrastructure.crypto.signatures import Ed25519SignatureVerifier

pytestmark = pytest.mark.unit

KEY = Ed25519PrivateKey.generate()
KEY_ID = "registry-unit-1"
PUBLIC = base64.b64encode(KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
VERIFIER = Ed25519SignatureVerifier({KEY_ID: PUBLIC})
AT = datetime(2026, 11, 20, 12, tzinfo=UTC)
MEMBER, SUBMISSION, CERTIFICATE = (uuid.UUID(int=n, version=4) for n in (1, 2, 3))
TOKENS = ("a" * 64, "b" * 64, "c" * 64)


def sign(body: dict[str, JsonValue]) -> str:
    return base64.b64encode(KEY.sign(canonical_json(body))).decode()


def certificate_body(**changes: JsonValue) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "schema_version": 1,
        "certificate_id": str(CERTIFICATE),
        "member_id": str(MEMBER),
        "submission_id": str(SUBMISSION),
        "kind": "PLEDGE",
        "key_version": "1.1",
        "as_of_seq": 40,
        "issued_at": "2026-11-20T12:00:00Z",
        "summary": {"checked": 3, "locked": 2, "held": 1, "released": 0, "rejected": 0},
        "items": [
            {"asset_token": TOKENS[0], "status": "LOCKED"},
            {"asset_token": TOKENS[1], "status": "HELD"},
            {"asset_token": TOKENS[2], "status": "LOCKED"},
        ],
    }
    body.update(changes)
    return body


def signed_certificate(body: dict[str, JsonValue], key_id: str = KEY_ID) -> SignedCertificate:
    return SignedCertificate(body, canonical_sha256(body), sign(body), key_id, AT)


EXPECTED = CertificateExpectation(
    CERTIFICATE,
    MEMBER,
    SUBMISSION,
    TapeKind.PLEDGE,
    "1.1",
    40,
    (
        (TOKENS[0], RegistryItemResult.LOCKED),
        (TOKENS[1], RegistryItemResult.CONFLICT),
        (TOKENS[2], RegistryItemResult.LOCKED),
    ),
)


def test_a_valid_certificate_verifies() -> None:
    assert check(signed_certificate(certificate_body())) is None


def test_each_certificate_reason_comes_from_the_first_failing_step() -> None:
    valid = signed_certificate(certificate_body())
    forged = certificate_body(summary={"checked": 3, "locked": 3, "held": 0, "released": 0, "rejected": 0})
    other = base64.b64decode(valid.signature)
    cases = {
        CertificateFailure.UNKNOWN_SIGNING_KEY: replace(valid, signing_key_id="registry-unit-2"),
        CertificateFailure.BODY_HASH_MISMATCH: replace(valid, body=forged),
        CertificateFailure.BAD_SIGNATURE: replace(
            valid, signature=base64.b64encode(bytes([other[0] ^ 1]) + other[1:]).decode()
        ),
        CertificateFailure.CONTENT_MISMATCH: signed_certificate(certificate_body(as_of_seq=41)),
    }
    for reason, certificate in cases.items():
        assert check(certificate) is reason, reason
    assert check(replace(valid, body={**valid.body, **json.loads('{"x": 1.5}')})) is (
        CertificateFailure.BODY_HASH_MISMATCH
    ), "a float cannot be canonical, so its hash cannot match"


def check(certificate: SignedCertificate) -> CertificateFailure | None:
    """The four steps with the delivered hash computed as `VerifyCertificate` does."""
    return verify_certificate(certificate, delivered_sha256(certificate.body), EXPECTED, VERIFIER)


def test_the_delivered_hash_is_the_canonical_body_hash_and_a_claim_that_differs_fails() -> None:
    valid = signed_certificate(certificate_body())
    assert delivered_sha256(valid.body) == valid.body_hash
    assert verify_certificate(valid, "0" * 64, EXPECTED, VERIFIER) is CertificateFailure.BODY_HASH_MISMATCH
    assert verify_certificate(valid, None, EXPECTED, VERIFIER) is CertificateFailure.BODY_HASH_MISMATCH


@pytest.mark.parametrize(
    "change",
    [
        {"certificate_id": str(uuid.uuid4())},
        {"member_id": str(uuid.uuid4())},
        {"submission_id": "not-a-uuid"},
        {"kind": "RELEASE"},
        {"key_version": "2.1"},
        {"as_of_seq": True},
        {"items": [{"asset_token": TOKENS[1], "status": "HELD"}]},
        {"items": [{"asset_token": t, "status": "LOCKED"} for t in TOKENS]},
        {"items": "LOCKED"},
        {"summary": {"checked": 3, "locked": 2, "held": 1, "released": 0}},
        {"summary": None},
    ],
    ids=lambda change: next(iter(change)),
)
def test_any_content_that_differs_from_what_the_bank_sent_and_was_told_is_a_mismatch(
    change: dict[str, JsonValue],
) -> None:
    assert content_matches(certificate_body(), EXPECTED)
    assert not content_matches(certificate_body(**change), EXPECTED)


def chain(count: int, start: ChainStart = GENESIS) -> list[EventHeader]:
    """`count` linked events after `start`, each hashed by the registry's header rule."""
    events, prev = [], start.prev_hash
    for seq in range(start.after_seq + 1, start.after_seq + count + 1):
        draft = EventHeader(seq, "SUBMISSION_RECORDED", f"{seq:064x}", prev, "", "2026-11-20T12:00:00Z", 1)
        event = replace(draft, event_hash=event_header_hash(draft))
        events.append(event)
        prev = event.event_hash
    return events


def signed_checkpoint(seq: int, last: int, head: str, key_id: str = KEY_ID) -> SignedCheckpoint:
    body: dict[str, JsonValue] = {
        "schema_version": 1,
        "checkpoint_seq": seq,
        "last_event_seq": last,
        "head_hash": head,
        "signed_at": "2026-11-20T12:00:00Z",
    }
    return SignedCheckpoint(body, sign(body), key_id, seq, last, head, AT)


def stored(checkpoint: SignedCheckpoint, failure: CheckpointFailure | None = None) -> Checkpoint:
    return Checkpoint(
        uuid.uuid4(), checkpoint.checkpoint_seq, checkpoint.last_event_seq, Sha256Hex(checkpoint.head_hash),
        AT, checkpoint.signature, checkpoint.signing_key_id, checkpoint.body, AT, AT, failure, None,
    )  # fmt: skip


EVENTS = chain(10)
FIRST = signed_checkpoint(1, 4, EVENTS[3].event_hash)
SECOND = signed_checkpoint(2, 10, EVENTS[9].event_hash)


def test_the_screening_steps_give_each_reason_in_order() -> None:
    previous = stored(FIRST)
    assert screen(SECOND, previous, None, VERIFIER).failure is None
    unknown = signed_checkpoint(2, 10, EVENTS[9].event_hash, key_id="registry-unit-2")
    assert screen(unknown, previous, None, VERIFIER).failure is CheckpointFailure.UNKNOWN_SIGNING_KEY
    tampered = replace(SECOND, body={**SECOND.body, "last_event_seq": 11})
    assert screen(tampered, previous, None, VERIFIER).failure is CheckpointFailure.BAD_SIGNATURE
    fork = signed_checkpoint(1, 4, "f" * 64)
    assert screen(fork, None, previous, VERIFIER).failure is CheckpointFailure.FORK
    longer = signed_checkpoint(1, 5, EVENTS[3].event_hash)
    assert screen(longer, None, previous, VERIFIER).failure is CheckpointFailure.FORK
    backwards = signed_checkpoint(2, 3, EVENTS[2].event_hash)
    assert screen(backwards, previous, None, VERIFIER).failure is CheckpointFailure.NON_MONOTONIC
    again = signed_checkpoint(1, 4, EVENTS[3].event_hash)
    assert screen(again, stored(SECOND), None, VERIFIER).failure is CheckpointFailure.NON_MONOTONIC


def test_a_segment_that_chains_to_the_head_passes_and_every_break_is_located() -> None:
    start = ChainStart(4, EVENTS[3].event_hash)
    segment = EVENTS[4:]
    assert check_segment(SECOND, start, segment).failure is None
    assert check_segment(FIRST, GENESIS, EVENTS[:4]).failure is None
    relinked = [*segment[:2], replace(segment[2], prev_hash="0" * 64), *segment[3:]]
    rehashed = [*segment[:3], replace(segment[3], payload_hash="1" * 64), *segment[4:]]
    gap = [*segment[:1], *segment[2:]]
    for broken, first_bad in ((relinked, 7), (rehashed, 8), (gap, 6), (segment[:3], 8), ([], 5)):
        verdict = check_segment(SECOND, start, broken)
        assert (verdict.failure, verdict.first_bad_event_seq) == (CheckpointFailure.CHAIN_BREAK, first_bad)
    wrong_start = check_segment(SECOND, ChainStart(4, ZERO_HASH), segment)
    assert (wrong_start.failure, wrong_start.first_bad_event_seq) == (CheckpointFailure.CHAIN_BREAK, 5)
    other_head = signed_checkpoint(2, 10, "e" * 64)
    verdict = check_segment(other_head, start, segment)
    assert (verdict.failure, verdict.first_bad_event_seq) == (CheckpointFailure.HEAD_MISMATCH, 10)
    same_seq = signed_checkpoint(2, 4, "d" * 64)
    assert check_segment(same_seq, start, []).failure is CheckpointFailure.HEAD_MISMATCH
    assert check_segment(signed_checkpoint(2, 4, EVENTS[3].event_hash), start, []).failure is None


def test_first_chain_break_ignores_events_beyond_the_last_seq() -> None:
    assert first_chain_break([*EVENTS, replace(EVENTS[0], seq=11)], GENESIS, 10) is None


def test_deep_verification_finds_a_rewrite_before_the_latest_checkpoint_and_names_its_range() -> None:
    rows = [stored(FIRST), stored(SECOND)]
    assert deep_findings(rows, EVENTS, VERIFIER) == []
    rewritten = [*EVENTS[:2], *chain(8, ChainStart(2, EVENTS[1].event_hash))]
    rewritten[2] = replace(rewritten[2], payload_hash="9" * 64)
    rewritten[2] = replace(rewritten[2], event_hash=event_header_hash(rewritten[2]))
    relinked = chain(7, ChainStart(3, rewritten[2].event_hash))
    findings = deep_findings(rows, [*rewritten[:3], *relinked], VERIFIER)
    assert [(f.failure, f.first_bad_event_seq, f.checkpoint.checkpoint_seq) for f in findings] == [
        (CheckpointFailure.HEAD_MISMATCH, 1, 1)
    ]
    edited = [*EVENTS[:6], replace(EVENTS[6], payload_hash="8" * 64), *EVENTS[7:]]
    findings = deep_findings(rows, edited, VERIFIER)
    assert [(f.failure, f.first_bad_event_seq, f.checkpoint.checkpoint_seq) for f in findings] == [
        (CheckpointFailure.CHAIN_BREAK, 7, 2)
    ]
    forged = replace(rows[0], signature=SECOND.signature)
    findings = deep_findings([forged, rows[1]], EVENTS, VERIFIER)
    assert [(f.failure, f.checkpoint.checkpoint_seq) for f in findings] == [
        (CheckpointFailure.BAD_SIGNATURE, 1)
    ]
    assert deep_findings([stored(FIRST, CheckpointFailure.FORK)], EVENTS, VERIFIER) == []
