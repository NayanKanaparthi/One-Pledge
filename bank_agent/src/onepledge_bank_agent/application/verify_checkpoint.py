"""`VerifyCheckpoint`: check a signed checkpoint from a CHECKPOINT message and store the verdict (SPEC 15.3).

`assess` works with no transaction open: duplicate delivery, key id, signature, fork,
monotonicity, and the hash continuity of the event headers from the last verified
checkpoint to this one (read from the registry in pages of 1,000). `record` stores the
checkpoint, verified or not, with the reason and the first bad event seq, and audits it.
Failed rows are never removed. A transient failure while reading headers raises and
stores nothing, so the message is delivered again.
"""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_bank_agent.application.events import LogEvent
from onepledge_bank_agent.application.ports import UnitOfWork, UnitOfWorkFactory
from onepledge_bank_agent.application.registry_audit import checkpoint_checked
from onepledge_bank_agent.application.registry_ports import RegistryClient, SignatureVerifier
from onepledge_bank_agent.application.runtime import Runtime
from onepledge_bank_agent.domain.checkpoint_rules import CheckpointVerdict, chain_start, check_segment, screen
from onepledge_bank_agent.domain.entities import Checkpoint
from onepledge_bank_agent.domain.registry_records import EventHeader, SignedCheckpoint
from onepledge_bank_agent.domain.values import Sha256Hex

HEADER_PAGE = 1000


@dataclass(frozen=True)
class Assessment:
    """The verdict on a checkpoint, or `duplicate` when exactly this checkpoint is already stored."""

    duplicate: bool
    verdict: CheckpointVerdict | None


class EventHeaderReader:
    """Reads event headers from the registry, page by page, up to a last seq."""

    def __init__(self, registry: RegistryClient) -> None:
        self._registry = registry

    def read(self, after_seq: int, last_seq: int) -> list[EventHeader]:
        """Every header with `after_seq < seq <= last_seq`, as far as the registry returns them."""
        events: list[EventHeader] = []
        cursor = after_seq
        while cursor < last_seq:
            page = self._registry.event_headers(cursor, HEADER_PAGE)
            fresh = [event for event in page.events if cursor < event.seq <= last_seq]
            if not fresh:
                break
            events.extend(fresh)
            cursor = max(event.seq for event in fresh)
        return events


class VerifyCheckpoint:
    """Verifies one received checkpoint and records the result."""

    def __init__(
        self,
        units: UnitOfWorkFactory,
        headers: EventHeaderReader,
        verifier: SignatureVerifier,
        runtime: Runtime,
    ) -> None:
        self._units = units
        self._headers = headers
        self._verifier = verifier
        self._clock = runtime.clock
        self._ids = runtime.ids
        self._log = runtime.log

    def assess(self, signed: SignedCheckpoint) -> Assessment:
        """Steps 1 to 6 of SPEC 15.3; reads headers from the registry when the checks before pass."""
        with self._units() as unit:
            if unit.evidence.checkpoints.delivered(signed.checkpoint_seq, signed.head_hash, signed.signature):
                return Assessment(True, None)
            previous = unit.evidence.checkpoints.latest_verified()
            same_seq = unit.evidence.checkpoints.verified_with_seq(signed.checkpoint_seq)
        verdict = screen(signed, previous, same_seq, self._verifier)
        if verdict.failure is None:
            start = chain_start(previous)
            events = self._headers.read(start.after_seq, signed.last_event_seq)
            verdict = check_segment(signed, start, events)
        return Assessment(False, verdict)

    def record(
        self, unit: UnitOfWork, signed: SignedCheckpoint, verdict: CheckpointVerdict
    ) -> Checkpoint | None:
        """Store the checkpoint with its verdict and audit it; None when it was stored meanwhile."""
        if unit.evidence.checkpoints.delivered(signed.checkpoint_seq, signed.head_hash, signed.signature):
            return None
        now = self._clock.now()
        checkpoint = Checkpoint(
            checkpoint_id=self._ids.new_id(),
            checkpoint_seq=signed.checkpoint_seq,
            last_event_seq=signed.last_event_seq,
            head_hash=Sha256Hex(signed.head_hash),
            signed_at=signed.signed_at,
            signature=signed.signature,
            signing_key_id=signed.signing_key_id,
            body=signed.body,
            received_at=now,
            verified_at=now,
            failure=verdict.failure,
            first_bad_event_seq=verdict.first_bad_event_seq,
        )
        unit.evidence.checkpoints.add(checkpoint)
        unit.record(checkpoint_checked(checkpoint))
        log = self._log.info if checkpoint.verified else self._log.warning
        log(
            LogEvent.CHECKPOINT_STORED,
            checkpoint_seq=checkpoint.checkpoint_seq,
            result="VERIFIED" if checkpoint.verified else None,
            reason_code=checkpoint.failure.value if checkpoint.failure else None,
        )
        return checkpoint
