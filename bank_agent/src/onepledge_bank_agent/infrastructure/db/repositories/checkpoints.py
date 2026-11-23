"""`SqlCheckpointRepository`: `checkpoints_seen` (insert-only)."""

from __future__ import annotations

from sqlalchemy import exists, insert, select
from sqlalchemy.orm import Session

from onepledge_bank_agent.domain.entities import Checkpoint
from onepledge_bank_agent.domain.enums import CheckpointFailure
from onepledge_bank_agent.domain.storable import StorableTimestamp
from onepledge_bank_agent.domain.values import Sha256Hex
from onepledge_bank_agent.infrastructure.db.models import CheckpointSeenRow
from onepledge_bank_agent.infrastructure.db.readable import read_rows

TABLE = "checkpoints_seen"


def _checkpoint(row: CheckpointSeenRow) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=row.checkpoint_id,
        checkpoint_seq=row.checkpoint_seq,
        last_event_seq=row.last_event_seq,
        head_hash=Sha256Hex(row.head_hash),
        signed_at=StorableTimestamp(row.signed_at),
        signature=row.signature,
        signing_key_id=row.signing_key_id,
        body=row.body,
        received_at=row.received_at,
        verified_at=row.verified_at,
        failure=CheckpointFailure(row.failure_reason) if row.failure_reason else None,
        first_bad_event_seq=row.first_bad_event_seq,
    )


class SqlCheckpointRepository:
    """Every checkpoint received, verified or kept as evidence of a failure."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def delivered(self, checkpoint_seq: int, head_hash: str, signature: str) -> bool:
        """This exact checkpoint is stored already."""
        row = CheckpointSeenRow
        statement = select(
            exists().where(
                row.checkpoint_seq == checkpoint_seq, row.head_hash == head_hash, row.signature == signature
            )
        )
        return bool(self._session.execute(statement).scalar_one())

    def latest_verified(self) -> Checkpoint | None:
        """The verified checkpoint with the highest seq."""
        statement = (
            select(CheckpointSeenRow)
            .where(CheckpointSeenRow.verified)
            .order_by(CheckpointSeenRow.checkpoint_seq.desc())
            .limit(1)
        )
        found = read_rows(self._session, statement, CheckpointSeenRow.checkpoint_id, _checkpoint, TABLE)
        return found[0] if found else None

    def verified_with_seq(self, checkpoint_seq: int) -> Checkpoint | None:
        """The verified checkpoint of a seq."""
        statement = select(CheckpointSeenRow).where(
            CheckpointSeenRow.verified, CheckpointSeenRow.checkpoint_seq == checkpoint_seq
        )
        found = read_rows(self._session, statement, CheckpointSeenRow.checkpoint_id, _checkpoint, TABLE)
        return found[0] if found else None

    def add(self, checkpoint: Checkpoint) -> None:
        """Insert a checkpoint with its verdict."""
        self._session.execute(
            insert(CheckpointSeenRow).values(
                checkpoint_id=checkpoint.checkpoint_id,
                checkpoint_seq=checkpoint.checkpoint_seq,
                last_event_seq=checkpoint.last_event_seq,
                head_hash=checkpoint.head_hash.value,
                signed_at=checkpoint.signed_at.value,
                signature=checkpoint.signature,
                signing_key_id=checkpoint.signing_key_id,
                body=dict(checkpoint.body),
                received_at=checkpoint.received_at,
                verified=checkpoint.verified,
                verified_at=checkpoint.verified_at,
                failure_reason=checkpoint.failure.value if checkpoint.failure else None,
                first_bad_event_seq=checkpoint.first_bad_event_seq,
            )
        )

    def any_failed(self) -> bool:
        """At least one checkpoint failed verification."""
        statement = select(exists().where(CheckpointSeenRow.verified.is_(False)))
        return bool(self._session.execute(statement).scalar_one())

    def all(self) -> list[Checkpoint]:
        """Every checkpoint by seq, then receipt."""
        statement = select(CheckpointSeenRow).order_by(
            CheckpointSeenRow.checkpoint_seq, CheckpointSeenRow.received_at, CheckpointSeenRow.checkpoint_id
        )
        return read_rows(self._session, statement, CheckpointSeenRow.checkpoint_id, _checkpoint, TABLE)
