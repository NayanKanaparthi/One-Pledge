"""Wire models of the registry answers the bank agent reads (`contracts/registry.yaml`), and their conversion.

Strict JSON types, unknown fields ignored. An item `result` is read as text, so a value
added later is seen and stored as REJECTED (UNKNOWN_RESULT) instead of breaking the
answer. Signed bodies are taken from the plain JSON, exactly as received.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import (
    AlertRole,
    BankPosition,
    CaseStatus,
    MatchBasis,
    MatchKind,
    TapeKind,
    TokenKind,
)
from onepledge_bank_agent.domain.registry_records import (
    AlertNotice,
    CaseNotice,
    CaseOutcome,
    EventHeader,
    EventPage,
    ItemOutcome,
    SignedCertificate,
    SignedCheckpoint,
)
from onepledge_bank_agent.infrastructure.http.answers import Strict, json_object

HASH = r"^[0-9a-f]{64}$"
SIGNATURE = r"^[A-Za-z0-9+/]{86}==$"
KEY_ID = r"^[a-z0-9-]{1,64}$"
KEY_VERSION = r"^[1-9][0-9]*\.[1-9][0-9]*$"
CODE = r"^[A-Z][A-Z0-9_]{0,63}$"
type Hash = Annotated[str, Field(pattern=HASH)]
type Seq = Annotated[int, Field(ge=0)]
type PositiveSeq = Annotated[int, Field(ge=1)]


class ItemResultBody(Strict):
    """`ItemResult`."""

    item_ref: uuid.UUID
    asset_token: Hash
    result: Annotated[str, Field(pattern=CODE)]
    lock_id: uuid.UUID | None
    already_held: bool
    match_kind: MatchKind | None
    match_basis: MatchBasis | None
    kinds_matched: list[TokenKind]
    case_id: uuid.UUID | None
    reason: Annotated[str, Field(pattern=CODE)] | None

    def outcome(self) -> ItemOutcome:
        """The domain record."""
        return ItemOutcome(
            self.item_ref, self.asset_token, self.result, self.lock_id, self.already_held, self.match_kind,
            self.match_basis, tuple(self.kinds_matched), self.case_id, self.reason,
        )  # fmt: skip


class SubmissionResultBody(Strict):
    """`SubmissionResult`."""

    submission_id: uuid.UUID
    member_id: uuid.UUID
    kind: TapeKind
    key_version: Annotated[str, Field(pattern=KEY_VERSION)]
    status: Literal["COMPLETED"]
    received_at: datetime
    as_of_seq: Seq
    certificate_id: uuid.UUID
    results: Annotated[list[ItemResultBody], Field(min_length=1, max_length=500)]


class SummaryBody(Strict):
    """`CertificateBody.summary`."""

    checked: PositiveSeq
    locked: Seq
    held: Seq
    released: Seq
    rejected: Seq


class CertificateItemBody(Strict):
    """`CertificateItem`."""

    asset_token: Hash
    status: Literal["LOCKED", "HELD", "RELEASED", "REJECTED"]


class CertificateBodyModel(Strict):
    """`CertificateBody`."""

    schema_version: Literal[1]
    certificate_id: uuid.UUID
    member_id: uuid.UUID
    submission_id: uuid.UUID
    kind: TapeKind
    key_version: Annotated[str, Field(pattern=KEY_VERSION)]
    as_of_seq: Seq
    issued_at: datetime
    summary: SummaryBody
    items: Annotated[list[CertificateItemBody], Field(min_length=1, max_length=500)]


class SignedCertificateBody(Strict):
    """`SignedCertificate`."""

    body: CertificateBodyModel
    body_hash: Hash
    signature: Annotated[str, Field(pattern=SIGNATURE)]
    signing_key_id: Annotated[str, Field(pattern=KEY_ID)]

    def record(self, document: Mapping[str, JsonValue]) -> SignedCertificate:
        """The domain record, with the body exactly as received."""
        body = json_object(document.get("body"))
        return SignedCertificate(
            body, self.body_hash, self.signature, self.signing_key_id, self.body.issued_at
        )


class CheckpointBodyModel(Strict):
    """`CheckpointBody`."""

    schema_version: Literal[1]
    checkpoint_seq: PositiveSeq
    last_event_seq: PositiveSeq
    head_hash: Hash
    signed_at: datetime


class SignedCheckpointBody(Strict):
    """`SignedCheckpoint`."""

    body: CheckpointBodyModel
    signature: Annotated[str, Field(pattern=SIGNATURE)]
    signing_key_id: Annotated[str, Field(pattern=KEY_ID)]

    def record(self, document: Mapping[str, JsonValue]) -> SignedCheckpoint:
        """The domain record, with the body exactly as received."""
        body = self.body
        return SignedCheckpoint(
            json_object(document.get("body")), self.signature, self.signing_key_id, body.checkpoint_seq,
            body.last_event_seq, body.head_hash, body.signed_at,
        )  # fmt: skip


class AlertPayloadBody(Strict):
    """`AlertMessage.payload`."""

    case_id: uuid.UUID
    role: AlertRole
    asset_token: Hash
    match_kind: MatchKind
    match_basis: MatchBasis
    kinds_matched: list[TokenKind]
    opened_seq: PositiveSeq

    def notice(self) -> AlertNotice:
        """The domain record."""
        return AlertNotice(
            self.case_id, self.role, self.asset_token, self.match_kind, self.match_basis,
            tuple(self.kinds_matched), self.opened_seq,
        )  # fmt: skip


class CasePayloadBody(Strict):
    """`CaseUpdateMessage.payload`."""

    case_id: uuid.UUID
    asset_token: Hash
    status: CaseStatus
    resolved_seq: PositiveSeq | None

    def notice(self) -> CaseNotice:
        """The domain record."""
        return CaseNotice(self.case_id, self.asset_token, self.status, self.resolved_seq)


class MessageEnvelope(Strict):
    """What every outbox message has, whatever its type."""

    message_id: uuid.UUID
    position: PositiveSeq
    type: Annotated[str, Field(min_length=1, max_length=64)]


class AlertMessageBody(Strict):
    """`AlertMessage`."""

    payload: AlertPayloadBody


class CaseMessageBody(Strict):
    """`CaseUpdateMessage`."""

    payload: CasePayloadBody


class CheckpointMessageBody(Strict):
    """`CheckpointMessage`."""

    payload: SignedCheckpointBody


class EventHeaderBody(Strict):
    """`LedgerEvent` without the payload."""

    seq: PositiveSeq
    event_type: Annotated[str, Field(min_length=1, max_length=64)]
    payload_hash: Hash
    prev_hash: Hash
    event_hash: Hash
    recorded_at: Annotated[str, Field(min_length=1, max_length=64)]
    schema_version: int

    def header(self) -> EventHeader:
        """The domain record (the time stays text: the hash is over the text as sent)."""
        return EventHeader(
            self.seq, self.event_type, self.payload_hash, self.prev_hash, self.event_hash, self.recorded_at,
            self.schema_version,
        )  # fmt: skip


class EventPageBody(Strict):
    """`LedgerEventPage`."""

    events: list[EventHeaderBody]
    head_seq: Seq

    def page(self) -> EventPage:
        """The domain record."""
        return EventPage(tuple(event.header() for event in self.events), self.head_seq)


class CaseViewBody(Strict):
    """`CaseView` (the fields the bank reads)."""

    case_id: uuid.UUID
    status: CaseStatus
    your_position: BankPosition | None

    def outcome(self) -> CaseOutcome:
        """The domain record."""
        return CaseOutcome(self.case_id, self.status, self.your_position)
