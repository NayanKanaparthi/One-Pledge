"""Bank audit log entries and their hash chain rule (SPEC 17.1).

`entry_hash = sha256(canonical_json({action, actor_type, actor_user_id, at, audit_id,
detail, object_id, object_type, outcome, prev_hash, request_id, seq}))`, with `at`
rendered `YYYY-MM-DDTHH:MM:SS.ffffffZ` and ids as lower case strings or null. The
first entry links to 64 zeros.

`detail` holds identifiers, codes, counts and hashes only, never a loan value.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from onepledge_bank_agent.domain.canonical_json import ZERO_HASH, JsonValue, canonical_sha256
from onepledge_bank_agent.domain.enums import ActorType, AuditAction, AuditObjectType, AuditOutcome, Role
from onepledge_bank_agent.domain.errors import InvalidValueError

GENESIS_PREV_HASH = ZERO_HASH


def render_audit_time(at: datetime) -> str:
    """`YYYY-MM-DDTHH:MM:SS.ffffffZ` in UTC, always six fraction digits."""
    if at.tzinfo is None:
        raise InvalidValueError("at", "NOT_TIMEZONE_AWARE")
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class Actor:
    """Who acts: a bank user with a role, or the system itself."""

    actor_type: ActorType
    user_id: uuid.UUID | None = None
    role: Role | None = None

    def __post_init__(self) -> None:
        """A user actor names the user and the role; the system names neither."""
        is_user = self.actor_type is ActorType.USER
        if is_user != (self.user_id is not None) or is_user != (self.role is not None):
            raise InvalidValueError("actor", "INCONSISTENT")

    @classmethod
    def system(cls) -> Actor:
        """The pipeline and other unattended work."""
        return cls(ActorType.SYSTEM)

    @classmethod
    def user(cls, user_id: uuid.UUID, role: Role) -> Actor:
        """A signed in bank user."""
        return cls(ActorType.USER, user_id, role)


@dataclass(frozen=True)
class AuditDraft:
    """An audit entry as a use case records it; the writer gives it id, time, seq and hashes."""

    action: AuditAction
    actor: Actor
    object_type: AuditObjectType
    object_id: uuid.UUID | None
    outcome: AuditOutcome = AuditOutcome.SUCCESS
    detail: Mapping[str, JsonValue] = field(default_factory=dict)
    request_id: str | None = None


@dataclass(frozen=True)
class AuditEntry:
    """One stored audit entry, linked into the chain."""

    audit_id: uuid.UUID
    seq: int
    at: datetime
    actor_type: ActorType
    actor_user_id: uuid.UUID | None
    action: str
    object_type: str
    object_id: uuid.UUID | None
    outcome: str
    request_id: str | None
    detail: Mapping[str, JsonValue]
    prev_hash: str
    entry_hash: str

    def hashed_fields(self) -> dict[str, JsonValue]:
        """The twelve fields the entry hash covers, in their JSON form."""
        return {
            "action": self.action,
            "actor_type": self.actor_type.value,
            "actor_user_id": str(self.actor_user_id) if self.actor_user_id else None,
            "at": render_audit_time(self.at),
            "audit_id": str(self.audit_id),
            "detail": dict(self.detail),
            "object_id": str(self.object_id) if self.object_id else None,
            "object_type": self.object_type,
            "outcome": self.outcome,
            "prev_hash": self.prev_hash,
            "request_id": self.request_id,
            "seq": self.seq,
        }

    def computed_hash(self) -> str:
        """The entry hash recomputed from the fields."""
        return canonical_sha256(self.hashed_fields())


def chain_entry(
    draft: AuditDraft, audit_id: uuid.UUID, at: datetime, previous: tuple[int, str] | None
) -> AuditEntry:
    """The entry that follows `previous` (its seq and entry hash), or the first entry when None."""
    seq, prev_hash = (previous[0] + 1, previous[1]) if previous is not None else (1, GENESIS_PREV_HASH)
    unhashed = AuditEntry(
        audit_id=audit_id,
        seq=seq,
        at=at,
        actor_type=draft.actor.actor_type,
        actor_user_id=draft.actor.user_id,
        action=draft.action.value,
        object_type=draft.object_type.value,
        object_id=draft.object_id,
        outcome=draft.outcome.value,
        request_id=draft.request_id,
        detail=dict(draft.detail),
        prev_hash=prev_hash,
        entry_hash=ZERO_HASH,
    )
    return replace(unhashed, entry_hash=unhashed.computed_hash())


def first_broken_seq(entries: Iterable[AuditEntry]) -> int | None:
    """The first seq where the chain fails (gap, wrong link or wrong hash), walking in seq order."""
    previous_hash = GENESIS_PREV_HASH
    for expected_seq, entry in enumerate(sorted(entries, key=lambda e: e.seq), start=1):
        linked = entry.seq == expected_seq and entry.prev_hash == previous_hash
        if not linked or entry.computed_hash() != entry.entry_hash:
            return expected_seq
        previous_hash = entry.entry_hash
    return None
