"""Signed checkpoints over the ledger head (TEST DOUBLE).

A checkpoint with `last_event_seq = n` carries the `event_hash` of event n. After
signing, the double appends a CHECKPOINT_SIGNED event, so the next checkpoint
covers the previous one.
"""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_contract_fakes.canonical_json import JsonValue
from onepledge_contract_fakes.errors import NoCheckpointError
from onepledge_contract_fakes.registry.ledger import SCHEMA_VERSION, Ledger
from onepledge_contract_fakes.registry.signing import RegistrySigner, flip_one_bit


@dataclass(frozen=True)
class SignedCheckpoint:
    """A checkpoint body with its signature."""

    body: dict[str, JsonValue]
    signature: str
    signing_key_id: str

    def to_json(self) -> dict[str, JsonValue]:
        """The `SignedCheckpoint` wire shape."""
        return {"body": dict(self.body), "signature": self.signature, "signing_key_id": self.signing_key_id}


class CheckpointBook:
    """Every checkpoint the double has signed, oldest first."""

    def __init__(self, signer: RegistrySigner) -> None:
        self._signer = signer
        self._checkpoints: list[SignedCheckpoint] = []

    def sign_head(self, ledger: Ledger, signed_at: str, corrupt_signature: bool = False) -> SignedCheckpoint:
        """Sign the current head. With `corrupt_signature` one bit of the signature is flipped."""
        body: dict[str, JsonValue] = {
            "schema_version": SCHEMA_VERSION,
            "checkpoint_seq": len(self._checkpoints) + 1,
            "last_event_seq": ledger.head_seq,
            "head_hash": ledger.head_hash,
            "signed_at": signed_at,
        }
        signature = self._signer.sign(body)
        if corrupt_signature:
            signature = flip_one_bit(signature)
        checkpoint = SignedCheckpoint(body=body, signature=signature, signing_key_id=self._signer.key_id)
        self._checkpoints.append(checkpoint)
        return checkpoint

    def latest(self) -> SignedCheckpoint:
        """The newest checkpoint, or NoCheckpointError."""
        if not self._checkpoints:
            raise NoCheckpointError
        return self._checkpoints[-1]

    def all(self) -> tuple[SignedCheckpoint, ...]:
        """Every checkpoint, oldest first."""
        return tuple(self._checkpoints)
