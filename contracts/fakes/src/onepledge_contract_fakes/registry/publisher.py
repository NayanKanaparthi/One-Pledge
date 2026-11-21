"""Ledger events and outbox fan-out shared by every registry operation (TEST DOUBLE).

- A new case queues one ALERT for the holder and one for the claimant, each with
  that party's own asset code.
- A resolved case queues one CASE_UPDATE for each party.
- Every checkpoint queues one CHECKPOINT for every member.
"""

from __future__ import annotations

from onepledge_contract_fakes.canonical_json import JsonValue
from onepledge_contract_fakes.clock import Clock, IdSource, rfc3339
from onepledge_contract_fakes.registry.cases import Case
from onepledge_contract_fakes.registry.checkpoints import SignedCheckpoint
from onepledge_contract_fakes.registry.model import EventType, MessageType
from onepledge_contract_fakes.registry.stores import RegistryStores


class Publisher:
    """Appends events and queues the messages that follow from them."""

    def __init__(
        self, stores: RegistryStores, member_ids: tuple[str, ...], clock: Clock, ids: IdSource
    ) -> None:
        self._ledger = stores.ledger
        self._checkpoints = stores.checkpoints
        self._outbox = stores.outbox
        self._member_ids = member_ids
        self._clock = clock
        self._ids = ids

    def now(self) -> str:
        """The current time in the wire format."""
        return rfc3339(self._clock.now())

    def event(self, event_type: EventType, payload: dict[str, JsonValue]) -> int:
        """Append one event and return its seq."""
        return self._ledger.append(event_type, payload, self.now()).seq

    def checkpoint(self, corrupt_signature: bool = False) -> SignedCheckpoint:
        """Sign the head, append CHECKPOINT_SIGNED and send the checkpoint to every member."""
        signed = self._checkpoints.sign_head(self._ledger, self.now(), corrupt_signature)
        self.event(
            EventType.CHECKPOINT_SIGNED,
            {
                "checkpoint_seq": signed.body["checkpoint_seq"],
                "last_event_seq": signed.body["last_event_seq"],
                "head_hash": signed.body["head_hash"],
            },
        )
        for member_id in self._member_ids:
            self._outbox.enqueue(
                self._ids.new_id(), member_id, MessageType.CHECKPOINT, signed.to_json(), self.now()
            )
        return signed

    def alerts(self, case: Case) -> None:
        """One ALERT per party of a new case."""
        for member_id in case.parties():
            role = case.role_of(member_id)
            payload: dict[str, JsonValue] = {
                "case_id": case.case_id,
                "role": role.value,
                "asset_token": case.asset_token_of(member_id),
                "match_kind": case.match_kind.value,
                "match_basis": case.match_basis.value,
                "kinds_matched": list(case.kinds_matched),
                "opened_seq": case.opened_seq,
            }
            self._enqueue(member_id, MessageType.ALERT, payload)

    def case_updates(self, case: Case) -> None:
        """One CASE_UPDATE per party of a resolved case."""
        for member_id in case.parties():
            payload: dict[str, JsonValue] = {
                "case_id": case.case_id,
                "asset_token": case.asset_token_of(member_id),
                "status": case.status.value,
                "resolved_seq": case.resolved_seq,
            }
            self._enqueue(member_id, MessageType.CASE_UPDATE, payload)

    def _enqueue(self, member_id: str, kind: MessageType, payload: dict[str, JsonValue]) -> None:
        self._outbox.enqueue(self._ids.new_id(), member_id, kind, payload, self.now())
