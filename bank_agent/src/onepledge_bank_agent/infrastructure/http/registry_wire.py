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

from pydantic import AfterValidator, Field

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
from onepledge_bank_agent.infrastructure.http.answers import (
    CanonicalUuid,
    Strict,
    base64_text,
    json_object,
)

HASH = r"^[0-9a-f]{64}$"
SIGNATURE = r"^[A-Za-z0-9+/]{86}==$"
KEY_ID = r"^[a-z0-9-]{1,64}$"
KEY_VERSION = r"^[1-9][0-9]*\.[1-9][0-9]*$"
CODE = r"^[A-Z][A-Z0-9_]{0,63}$"
BIGINT_MAX = 9_223_372_036_854_775_807
"""The largest sequence number or position the bank can store (SPEC_DECISIONS 85, rule 1)."""
type Hash = Annotated[str, Field(pattern=HASH)]
type Signature = Annotated[str, Field(pattern=SIGNATURE), AfterValidator(base64_text)]
"""An Ed25519 signature: 88 characters of canonical standard base64 (SPEC_DECISIONS 75)."""
type Seq = Annotated[int, Field(ge=0, le=BIGINT_MAX)]
type PositiveSeq = Annotated[int, Field(ge=1, le=BIGINT_MAX)]


def _distinct(kinds: list[TokenKind]) -> list[TokenKind]:
    """`uniqueItems: true`: a kind listed twice breaks the contract."""
    if len(set(kinds)) != len(kinds):
        raise ValueError("kinds_matched")
    return kinds


type Kinds = Annotated[list[TokenKind], AfterValidator(_distinct)]


class ItemResultBody(Strict):
    """`ItemResult`."""

    item_ref: CanonicalUuid
    asset_token: Hash
    result: Annotated[str, Field(pattern=CODE)]
    lock_id: CanonicalUuid | None
    already_held: bool
    match_kind: MatchKind | None
    match_basis: MatchBasis | None
    kinds_matched: Kinds
    case_id: CanonicalUuid | None
    reason: Annotated[str, Field(pattern=CODE)] | None

    def outcome(self) -> ItemOutcome:
        """The domain record."""
        return ItemOutcome(
            self.item_ref, self.asset_token, self.result, self.lock_id, self.already_held, self.match_kind,
            self.match_basis, tuple(self.kinds_matched), self.case_id, self.reason,
        )  # fmt: skip


class SubmissionResultBody(Strict):
    """`SubmissionResult`."""

    submission_id: CanonicalUuid
    member_id: CanonicalUuid
    kind: TapeKind
    key_version: Annotated[str, Field(pattern=KEY_VERSION)]
    status: Literal["COMPLETED"]
    received_at: datetime
    as_of_seq: Seq
    certificate_id: CanonicalUuid
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
    certificate_id: CanonicalUuid
    member_id: CanonicalUuid
    submission_id: CanonicalUuid
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
    signature: Signature
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
    signature: Signature
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

    case_id: CanonicalUuid
    role: AlertRole
    asset_token: Hash
    match_kind: MatchKind
    match_basis: MatchBasis
    kinds_matched: Kinds
    opened_seq: PositiveSeq

    def notice(self) -> AlertNotice:
        """The domain record."""
        return AlertNotice(
            self.case_id, self.role, self.asset_token, self.match_kind, self.match_basis,
            tuple(self.kinds_matched), self.opened_seq,
        )  # fmt: skip


class CasePayloadBody(Strict):
    """`CaseUpdateMessage.payload`."""

    case_id: CanonicalUuid
    asset_token: Hash
    status: CaseStatus
    resolved_seq: PositiveSeq | None

    def notice(self) -> CaseNotice:
        """The domain record."""
        return CaseNotice(self.case_id, self.asset_token, self.status, self.resolved_seq)


class MessageEnvelope(Strict):
    """What the bank needs to acknowledge any outbox message, whatever its type and the rest of it. The id
    is read in any spelling the uuid module parses, so a message whose id is not canonical can still be
    quarantined and acknowledged; `MessageBody` and `message_id_is_canonical` refuse that spelling."""

    message_id: uuid.UUID
    type: Annotated[str, Field(min_length=1, max_length=64)]


class MessageBody(Strict):
    """The fields every outbox message of a known type has (`registry.yaml`)."""

    message_id: CanonicalUuid
    position: PositiveSeq
    created_at: datetime


class AlertMessageBody(MessageBody):
    """`AlertMessage`."""

    type: Literal["ALERT"]
    payload: AlertPayloadBody


class CaseMessageBody(MessageBody):
    """`CaseUpdateMessage`."""

    type: Literal["CASE_UPDATE"]
    payload: CasePayloadBody


class CheckpointMessageBody(MessageBody):
    """`CheckpointMessage`."""

    type: Literal["CHECKPOINT"]
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

    case_id: CanonicalUuid
    status: CaseStatus
    your_position: BankPosition | None

    def outcome(self) -> CaseOutcome:
        """The domain record."""
        return CaseOutcome(self.case_id, self.status, self.your_position)
