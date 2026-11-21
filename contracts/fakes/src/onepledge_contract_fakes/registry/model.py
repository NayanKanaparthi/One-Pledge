"""Value types of the registry double (TEST DOUBLE)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from onepledge_contract_fakes.canonical_json import JsonValue

TOKEN_KINDS: tuple[str, ...] = (
    "ASSET",
    "VIN",
    "BORROWER",
    "AMOUNT_EXACT",
    "AMOUNT_100",
    "AMOUNT_1000",
    "DATE_DAY",
    "DATE_MONTH",
)
FIELD_KINDS: tuple[str, ...] = TOKEN_KINDS[1:]


class SubmissionKind(StrEnum):
    """What a submission asks for."""

    BACK_BOOK = "BACK_BOOK"
    PLEDGE = "PLEDGE"
    RELEASE = "RELEASE"


class ItemOutcome(StrEnum):
    """Per item result of a submission."""

    LOCKED = "LOCKED"
    CONFLICT = "CONFLICT"
    RELEASED = "RELEASED"
    REJECTED = "REJECTED"


class MatchKind(StrEnum):
    """How strongly a conflicting item agrees with the lock it matched."""

    EXACT = "EXACT"
    LIKELY = "LIKELY"


class MatchBasis(StrEnum):
    """Which rule produced a conflict: the asset code (Rule B) or field agreement (Rule C)."""

    ASSET = "ASSET"
    FIELDS = "FIELDS"


class CaseStatus(StrEnum):
    """Life cycle of a conflict case."""

    OPEN = "OPEN"
    CLEARED_DATA_ERROR = "CLEARED_DATA_ERROR"
    CLEARED_LATE_RELEASE = "CLEARED_LATE_RELEASE"
    CONFIRMED = "CONFIRMED"


class Position(StrEnum):
    """A party's position on a case."""

    DATA_ERROR = "DATA_ERROR"
    CONFIRMED_DOUBLE_PLEDGE = "CONFIRMED_DOUBLE_PLEDGE"


class Role(StrEnum):
    """A member's side of a case."""

    HOLDER = "HOLDER"
    CLAIMANT = "CLAIMANT"


class EventType(StrEnum):
    """Ledger event types of `registry.yaml`."""

    MEMBER_JOINED = "MEMBER_JOINED"
    LOCK_ACQUIRED = "LOCK_ACQUIRED"
    LOCK_RELEASED = "LOCK_RELEASED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    CASE_OPENED = "CASE_OPENED"
    CASE_RESOLVED = "CASE_RESOLVED"
    CERTIFICATE_ISSUED = "CERTIFICATE_ISSUED"
    CHECKPOINT_SIGNED = "CHECKPOINT_SIGNED"


class MessageType(StrEnum):
    """Outbox message types."""

    ALERT = "ALERT"
    CASE_UPDATE = "CASE_UPDATE"
    CHECKPOINT = "CHECKPOINT"


@dataclass(frozen=True)
class SubmissionItem:
    """One item of a submission: the asset code and, for locks, the field codes."""

    item_ref: str
    asset_token: str
    field_tokens: Mapping[str, str] | None


@dataclass(frozen=True)
class Submission:
    """A validated submission request."""

    kind: SubmissionKind
    key_version: str
    items: tuple[SubmissionItem, ...]


@dataclass(frozen=True)
class ItemResult:
    """The decision for one item, as returned to the submitting member."""

    item_ref: str
    asset_token: str
    result: ItemOutcome
    lock_id: str | None = None
    already_held: bool = False
    match_kind: MatchKind | None = None
    match_basis: MatchBasis | None = None
    kinds_matched: tuple[str, ...] = ()
    case_id: str | None = None
    reason: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        """The `ItemResult` wire shape."""
        return {
            "item_ref": self.item_ref,
            "asset_token": self.asset_token,
            "result": self.result.value,
            "lock_id": self.lock_id,
            "already_held": self.already_held,
            "match_kind": self.match_kind.value if self.match_kind else None,
            "match_basis": self.match_basis.value if self.match_basis else None,
            "kinds_matched": list(self.kinds_matched),
            "case_id": self.case_id,
            "reason": self.reason,
        }
