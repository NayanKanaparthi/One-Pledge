"""The append-only, hash chained ledger of the registry double (TEST DOUBLE).

Hash rules of `contracts/registry.yaml`: `payload_hash` is the SHA-256 of the
canonical payload; `event_hash` is the SHA-256 of the canonical header with the
keys `event_type`, `payload_hash`, `prev_hash`, `recorded_at`, `schema_version`,
`seq`; `prev_hash` of seq 1 is 64 zeros.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace

from onepledge_contract_fakes.canonical_json import JsonValue, canonical_sha256
from onepledge_contract_fakes.errors import ErrorDetail, ValidationFailedError
from onepledge_contract_fakes.registry.model import EventType

GENESIS_HASH = "0" * 64
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LedgerEvent:
    """One stored ledger event."""

    seq: int
    event_type: str
    payload: dict[str, JsonValue]
    payload_hash: str
    prev_hash: str
    event_hash: str
    recorded_at: str
    schema_version: int = SCHEMA_VERSION

    def header(self) -> dict[str, JsonValue]:
        """The `LedgerEvent` wire shape without the payload."""
        return {
            "seq": self.seq,
            "event_type": self.event_type,
            "payload_hash": self.payload_hash,
            "prev_hash": self.prev_hash,
            "event_hash": self.event_hash,
            "recorded_at": self.recorded_at,
            "schema_version": self.schema_version,
        }

    def full(self) -> dict[str, JsonValue]:
        """The wire shape with the payload (auditor view)."""
        return {**self.header(), "payload": copy.deepcopy(self.payload)}


def event_hash(seq: int, event_type: str, payload_hash: str, prev_hash: str, recorded_at: str) -> str:
    """SHA-256 of the canonical header of an event."""
    header: dict[str, JsonValue] = {
        "event_type": event_type,
        "payload_hash": payload_hash,
        "prev_hash": prev_hash,
        "recorded_at": recorded_at,
        "schema_version": SCHEMA_VERSION,
        "seq": seq,
    }
    return canonical_sha256(header)


class Ledger:
    """Events with gapless sequence numbers starting at 1."""

    def __init__(self) -> None:
        self._events: list[LedgerEvent] = []

    @property
    def head_seq(self) -> int:
        """Seq of the newest event, 0 when empty."""
        return len(self._events)

    @property
    def head_hash(self) -> str:
        """Hash of the newest event, 64 zeros when empty."""
        return self._events[-1].event_hash if self._events else GENESIS_HASH

    def append(self, event_type: EventType, payload: dict[str, JsonValue], recorded_at: str) -> LedgerEvent:
        """Append one event and chain it to the head."""
        seq = self.head_seq + 1
        payload_copy = copy.deepcopy(payload)
        payload_hash = canonical_sha256(payload_copy)
        prev = self.head_hash
        event = LedgerEvent(
            seq=seq,
            event_type=event_type.value,
            payload=payload_copy,
            payload_hash=payload_hash,
            prev_hash=prev,
            event_hash=event_hash(seq, event_type.value, payload_hash, prev, recorded_at),
            recorded_at=recorded_at,
        )
        self._events.append(event)
        return event

    def after(self, after_seq: int, limit: int) -> tuple[LedgerEvent, ...]:
        """Events with seq greater than `after_seq`, ascending, at most `limit`."""
        return tuple(self._events[after_seq : after_seq + limit])

    def all(self) -> tuple[LedgerEvent, ...]:
        """Every event, ascending."""
        return tuple(self._events)

    def rewrite_history(self, seq: int) -> None:
        """Tamper: change one payload and recompute every hash from there to the head."""
        index = self._index(seq)
        original = self._events[index]
        payload = {**copy.deepcopy(original.payload), "rewritten": True}
        prev = original.prev_hash
        for position in range(index, len(self._events)):
            current = self._events[position]
            new_payload = payload if position == index else current.payload
            payload_hash = canonical_sha256(new_payload)
            new_hash = event_hash(current.seq, current.event_type, payload_hash, prev, current.recorded_at)
            self._events[position] = replace(
                current, payload=new_payload, payload_hash=payload_hash, prev_hash=prev, event_hash=new_hash
            )
            prev = new_hash

    def edit_in_place(self, seq: int) -> None:
        """Tamper: change one stored `payload_hash` and nothing else."""
        index = self._index(seq)
        current = self._events[index]
        flipped = ("1" if current.payload_hash[0] == "0" else "0") + current.payload_hash[1:]
        self._events[index] = replace(current, payload_hash=flipped)

    def _index(self, seq: int) -> int:
        if not 1 <= seq <= self.head_seq:
            raise ValidationFailedError(details=(ErrorDetail("seq", "OUT_OF_RANGE"),))
        return seq - 1
