"""Per member outbox with at least once delivery (TEST DOUBLE).

Positions are per member and strictly increasing from 1. A message is returned on
every poll until the member acknowledges it.
"""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_contract_fakes.canonical_json import JsonValue
from onepledge_contract_fakes.errors import OutboxMessageNotFoundError
from onepledge_contract_fakes.registry.model import MessageType


@dataclass(frozen=True)
class OutboxMessage:
    """One message addressed to one member."""

    message_id: str
    member_id: str
    position: int
    type: MessageType
    created_at: str
    payload: dict[str, JsonValue]

    def to_json(self) -> dict[str, JsonValue]:
        """The wire shape of `AlertMessage`, `CaseUpdateMessage` or `CheckpointMessage`."""
        return {
            "message_id": self.message_id,
            "position": self.position,
            "type": self.type.value,
            "created_at": self.created_at,
            "payload": self.payload,
        }


class Outbox:
    """Queued messages and acknowledgements of every member."""

    def __init__(self) -> None:
        self._messages: dict[str, list[OutboxMessage]] = {}
        self._acked: set[str] = set()

    def enqueue(
        self,
        message_id: str,
        member_id: str,
        kind: MessageType,
        payload: dict[str, JsonValue],
        created_at: str,
    ) -> OutboxMessage:
        """Queue one message at the member's next position."""
        queue = self._messages.setdefault(member_id, [])
        message = OutboxMessage(message_id, member_id, len(queue) + 1, kind, created_at, payload)
        queue.append(message)
        return message

    def pending(self, member_id: str, limit: int) -> tuple[tuple[OutboxMessage, ...], bool]:
        """Unacknowledged messages in position order, and whether more remain."""
        waiting = [m for m in self._messages.get(member_id, []) if m.message_id not in self._acked]
        return tuple(waiting[:limit]), len(waiting) > limit

    def ack(self, member_id: str, message_id: str) -> None:
        """Acknowledge a message of this member. Repeating is harmless."""
        if not any(m.message_id == message_id for m in self._messages.get(member_id, [])):
            raise OutboxMessageNotFoundError
        self._acked.add(message_id)

    def all(self) -> tuple[OutboxMessage, ...]:
        """Every message ever queued, by member then position."""
        return tuple(m for member in sorted(self._messages) for m in self._messages[member])
