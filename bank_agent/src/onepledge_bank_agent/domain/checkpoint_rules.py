"""Verification of registry checkpoints and of the ledger hash chain (SPEC 15.3, 15.6), as pure rules.

A received checkpoint is checked in this order: key id configured (UNKNOWN_SIGNING_KEY),
signature (BAD_SIGNATURE), no verified checkpoint with the same seq but another head
(FORK), it extends the last verified one (NON_MONOTONIC), and the event headers from
the last verified checkpoint to this one chain correctly (CHAIN_BREAK) and end at its
head (HEAD_MISMATCH). Deep verification walks the whole chain from seq 1 and checks
every stored verified checkpoint against it, which also finds a history rewrite that
happened before the bank's latest checkpoint.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from onepledge_bank_agent.domain.canonical_json import ZERO_HASH, JsonValue, canonical_sha256
from onepledge_bank_agent.domain.certificate_rules import canonical_body
from onepledge_bank_agent.domain.entities import Checkpoint
from onepledge_bank_agent.domain.enums import CheckpointFailure
from onepledge_bank_agent.domain.registry_records import EventHeader, SignedCheckpoint
from onepledge_bank_agent.domain.signatures import SignatureVerifier


@dataclass(frozen=True)
class CheckpointVerdict:
    """The outcome of a check: a failure reason (None when it passed) and the first bad event seq."""

    failure: CheckpointFailure | None
    first_bad_event_seq: int | None = None


@dataclass(frozen=True)
class ChainStart:
    """Where a chain segment starts: the seq before it and the hash that seq must have."""

    after_seq: int
    prev_hash: str


PASSED = CheckpointVerdict(None)
GENESIS = ChainStart(0, ZERO_HASH)
CHAIN = CheckpointFailure.CHAIN_BREAK


def event_header_hash(event: EventHeader) -> str:
    """SHA-256 of the canonical header (the six keys of `registry.yaml`)."""
    header: dict[str, JsonValue] = {
        "event_type": event.event_type,
        "payload_hash": event.payload_hash,
        "prev_hash": event.prev_hash,
        "recorded_at": event.recorded_at,
        "schema_version": event.schema_version,
        "seq": event.seq,
    }
    return canonical_sha256(header)


def signature_failure(
    body: Mapping[str, JsonValue], key_id: str, signature: str, verifier: SignatureVerifier
) -> CheckpointFailure | None:
    """UNKNOWN_SIGNING_KEY or BAD_SIGNATURE, or None when the signature over the body verifies."""
    if not verifier.knows(key_id):
        return CheckpointFailure.UNKNOWN_SIGNING_KEY
    message = canonical_body(body)
    if message is None or not verifier.verifies(key_id, message, signature):
        return CheckpointFailure.BAD_SIGNATURE
    return None


def chain_start(previous: Checkpoint | None) -> ChainStart:
    """The segment to fetch for a new checkpoint: after the last verified one, or from the genesis."""
    if previous is None:
        return GENESIS
    return ChainStart(previous.last_event_seq, previous.head_hash.value)


def screen(
    checkpoint: SignedCheckpoint,
    previous: Checkpoint | None,
    verified_same_seq: Checkpoint | None,
    verifier: SignatureVerifier,
) -> CheckpointVerdict:
    """Steps 2 to 5 of SPEC 15.3, which need no ledger read."""
    failure = signature_failure(checkpoint.body, checkpoint.signing_key_id, checkpoint.signature, verifier)
    if failure is not None:
        return CheckpointVerdict(failure)
    if verified_same_seq is not None and (
        verified_same_seq.head_hash.value != checkpoint.head_hash
        or verified_same_seq.last_event_seq != checkpoint.last_event_seq
    ):
        return CheckpointVerdict(CheckpointFailure.FORK)
    if previous is not None and (
        checkpoint.checkpoint_seq <= previous.checkpoint_seq
        or checkpoint.last_event_seq < previous.last_event_seq
    ):
        return CheckpointVerdict(CheckpointFailure.NON_MONOTONIC)
    return PASSED


def first_chain_break(events: Sequence[EventHeader], start: ChainStart, last_seq: int) -> int | None:
    """The first seq in `(start.after_seq, last_seq]` that is missing, out of order, not linked to the
    previous event, or whose hash does not match its header; None when the whole segment holds."""
    expected_seq, prev_hash = start.after_seq + 1, start.prev_hash
    for event in events:
        if expected_seq > last_seq:
            break
        linked = event.seq == expected_seq and event.prev_hash == prev_hash
        if not linked or event_header_hash(event) != event.event_hash:
            return expected_seq
        prev_hash, expected_seq = event.event_hash, expected_seq + 1
    return expected_seq if expected_seq <= last_seq else None


def check_segment(
    checkpoint: SignedCheckpoint, start: ChainStart, events: Sequence[EventHeader]
) -> CheckpointVerdict:
    """Step 6: the segment after `start` chains and ends at the checkpoint's head."""
    last = checkpoint.last_event_seq
    if last == start.after_seq:
        if checkpoint.head_hash == start.prev_hash:
            return PASSED
        return CheckpointVerdict(CheckpointFailure.HEAD_MISMATCH, last)
    broken = first_chain_break(events, start, last)
    if broken is not None:
        return CheckpointVerdict(CheckpointFailure.CHAIN_BREAK, broken)
    head = next(event for event in events if event.seq == last)
    if head.event_hash != checkpoint.head_hash:
        return CheckpointVerdict(CheckpointFailure.HEAD_MISMATCH, last)
    return PASSED


@dataclass(frozen=True)
class DeepFinding:
    """A problem deep verification found, and the stored checkpoint it concerns."""

    checkpoint: Checkpoint
    failure: CheckpointFailure
    first_bad_event_seq: int | None


def _chain_finding(
    verified: Sequence[Checkpoint], events: Sequence[EventHeader], last: int
) -> DeepFinding | None:
    """The first chain violation up to `last`, attributed to the verified checkpoint covering it."""
    findings: list[DeepFinding] = []
    broken = first_chain_break(events, GENESIS, last)
    if broken is not None:
        covering = next((row for row in verified if row.last_event_seq >= broken), verified[-1])
        findings.append(DeepFinding(covering, CheckpointFailure.CHAIN_BREAK, broken))
    hashes = {event.seq: event.event_hash for event in events}
    range_start = 1
    for row in verified:
        if hashes.get(row.last_event_seq) != row.head_hash.value:
            findings.append(DeepFinding(row, CheckpointFailure.HEAD_MISMATCH, range_start))
            break
        range_start = row.last_event_seq + 1
    findings.sort(key=lambda finding: (finding.first_bad_event_seq or 0, finding.failure is not CHAIN))
    return findings[0] if findings else None


def deep_findings(
    stored: Sequence[Checkpoint], events: Sequence[EventHeader], verifier: SignatureVerifier
) -> list[DeepFinding]:
    """Every stored verified signature that no longer verifies, then the first chain violation."""
    verified = sorted((row for row in stored if row.verified), key=lambda row: row.checkpoint_seq)
    findings = [
        DeepFinding(row, failure, None)
        for row in verified
        if (failure := signature_failure(row.body, row.signing_key_id, row.signature, verifier)) is not None
    ]
    if verified:
        chain = _chain_finding(verified, events, ledger_end(stored))
        if chain is not None:
            findings.append(chain)
    return findings


def ledger_end(stored: Sequence[Checkpoint]) -> int:
    """The highest `last_event_seq` of any stored checkpoint (0 when none): the end of deep verification."""
    return max((row.last_event_seq for row in stored), default=0)
