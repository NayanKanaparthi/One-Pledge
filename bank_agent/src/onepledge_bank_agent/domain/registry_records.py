"""What the registry and the key holders answer, as plain records (`registry.yaml`, `key_holder.yaml`).

The infrastructure clients validate every answer against the contract shape and turn
it into these records. Signed bodies are kept exactly as received (`body`), because a
signature is over those bytes and nothing else. Only codes and registry identifiers
appear here; the registry never holds a raw loan value.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import (
    AlertRole,
    BankPosition,
    CaseStatus,
    MatchBasis,
    MatchKind,
    QuarantineReason,
    TapeKind,
    TokenKind,
)
from onepledge_bank_agent.domain.storable import StorableTimestamp
from onepledge_bank_agent.domain.values import KeyVersion


@dataclass(frozen=True)
class RegistryItem:
    """One item the bank sends: its reference, its asset code and, for a lock request, the field codes."""

    item_ref: uuid.UUID
    asset_token: str
    field_tokens: Mapping[TokenKind, str] | None


@dataclass(frozen=True)
class RegistrySubmission:
    """The body of `POST /v1/submissions`, built only from stored rows so every attempt sends the same."""

    kind: TapeKind
    key_version: str
    items: tuple[RegistryItem, ...]


@dataclass(frozen=True)
class Evaluation:
    """One key holder answer: the key version it used and the evaluated elements in request order."""

    key_version: int
    elements: tuple[str, ...]


@dataclass(frozen=True)
class ItemOutcome:
    """The registry's result for one submitted item. `result` is kept as sent, so an unknown value is seen."""

    item_ref: uuid.UUID
    asset_token: str
    result: str
    lock_id: uuid.UUID | None
    already_held: bool
    match_kind: MatchKind | None
    match_basis: MatchBasis | None
    kinds_matched: tuple[TokenKind, ...]
    case_id: uuid.UUID | None
    reason: str | None


@dataclass(frozen=True)
class RegistryAnswer:
    """A 201 decision or a 200 idempotent replay of `POST /v1/submissions`."""

    status: int
    replay: bool
    submission_id: uuid.UUID
    member_id: uuid.UUID
    kind: TapeKind
    key_version: KeyVersion
    as_of_seq: int
    certificate_id: uuid.UUID
    results: tuple[ItemOutcome, ...]
    body: Mapping[str, JsonValue]


@dataclass(frozen=True)
class SignedCertificate:
    """A certificate exactly as the registry sent it: body, its hash, the signature and the key id."""

    body: Mapping[str, JsonValue]
    body_hash: str
    signature: str
    signing_key_id: str
    issued_at: StorableTimestamp


@dataclass(frozen=True)
class SignedCheckpoint:
    """A signed checkpoint: the body as received and the fields the bank compares."""

    body: Mapping[str, JsonValue]
    signature: str
    signing_key_id: str
    checkpoint_seq: int
    last_event_seq: int
    head_hash: str
    signed_at: StorableTimestamp


@dataclass(frozen=True)
class EventHeader:
    """One ledger event without its payload (`view=headers`)."""

    seq: int
    event_type: str
    payload_hash: str
    prev_hash: str
    event_hash: str
    recorded_at: str
    schema_version: int


@dataclass(frozen=True)
class EventPage:
    """A page of event headers and the ledger head at the time of the call."""

    events: tuple[EventHeader, ...]
    head_seq: int


@dataclass(frozen=True)
class AlertNotice:
    """The payload of an ALERT message: this bank's own asset code and the match facts."""

    case_id: uuid.UUID
    role: AlertRole
    asset_token: str
    match_kind: MatchKind
    match_basis: MatchBasis
    kinds_matched: tuple[TokenKind, ...]
    opened_seq: int


@dataclass(frozen=True)
class CaseNotice:
    """The payload of a CASE_UPDATE message."""

    case_id: uuid.UUID
    asset_token: str
    status: CaseStatus
    resolved_seq: int | None


@dataclass(frozen=True)
class MessageEvidence:
    """An outbox message's payload as received, kept for quarantine, and the SHA-256 of its serialised form.

    The payload is withheld (kept as its hash alone, `payload` None) when its serialised
    form exceeds 64 KiB (`oversized`) or when it holds a value the bank cannot store and read
    back (SPEC_DECISIONS 100).
    """

    payload: JsonValue
    payload_sha256: str
    oversized: bool = False
    withheld: bool = False


@dataclass(frozen=True)
class QuarantinedPayload:
    """The payload of a known message type that breaks the contract, kept as evidence (SPEC_DECISIONS 57, 85).

    `payload` is the payload as received. When `withheld` it is None and only
    `payload_sha256` is kept: always for reason OVERSIZED (over 64 KiB), and for a payload
    holding text the bank cannot store. `payload_sha256` is always the hash of the
    serialised form.
    """

    reason: QuarantineReason
    payload: JsonValue
    payload_sha256: str
    withheld: bool = False


type MessagePayload = AlertNotice | CaseNotice | SignedCheckpoint | QuarantinedPayload | None


@dataclass(frozen=True)
class OutboxMessage:
    """One outbox message. `payload` is None for a message type this bank does not know, and a
    `QuarantinedPayload` for a known type that failed validation (SPEC_DECISIONS 85, rule 1).
    `position` is None when the registry sent one that does not fit a `bigint`."""

    message_id: uuid.UUID
    position: int | None
    message_type: str
    payload: MessagePayload
    evidence: MessageEvidence


@dataclass(frozen=True)
class UnacknowledgeableElement:
    """An element of an outbox page that is not an object or has no canonical message id, so it cannot
    be acknowledged (SPEC_DECISIONS 101): kept as its evidence, recorded once by its hash."""

    evidence: MessageEvidence


type OutboxElement = OutboxMessage | UnacknowledgeableElement


@dataclass(frozen=True)
class OutboxPage:
    """Unacknowledged elements in position order, and whether more are waiting."""

    elements: tuple[OutboxElement, ...]
    has_more: bool


@dataclass(frozen=True)
class CaseOutcome:
    """The case as the registry reports it after this bank recorded its position."""

    case_id: uuid.UUID
    status: CaseStatus
    your_position: BankPosition | None


@dataclass(frozen=True)
class QuarantinedMessage:
    """A row of `registry_messages_quarantined`: the message, why it was quarantined, and when. The message
    id is None for an element that had no usable one (UNACKNOWLEDGEABLE)."""

    quarantine_id: uuid.UUID
    message_id: uuid.UUID | None
    position: int | None
    message_type: str
    quarantined: QuarantinedPayload
    received_at: datetime
