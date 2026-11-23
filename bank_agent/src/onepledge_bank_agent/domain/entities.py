"""Entities without a state machine of their own (SPEC 8.2): reference data, users and registry proofs."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import (
    CertificateFailure,
    CheckpointFailure,
    FacilityStatus,
    RequestOrigin,
    Role,
    TapeKind,
)
from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.registry_records import SignedCertificate
from onepledge_bank_agent.domain.storable import EARLIEST_DATE, LATEST_DATE, StorableTimestamp
from onepledge_bank_agent.domain.values import KeyVersion, Lei, Sha256Hex

_FACILITY_CODE = re.compile(r"^[A-Z0-9-]{3,40}$")
MAX_ADVANCE_RATE_BPS = 10_000
MAX_CREDIT_LIMIT_CENTS = 2**63 - 1
"""`credit_limit_cents` is a `bigint` (SPEC 12.1)."""
MAX_ITEMS_PER_REQUEST = 500


@dataclass(frozen=True)
class Lender:
    """A non-bank lender this bank funds."""

    lender_id: uuid.UUID
    legal_name: str
    lei: Lei

    def __post_init__(self) -> None:
        """A lender has a non-empty legal name."""
        if not self.legal_name.strip():
            raise InvalidValueError("legal_name", "REQUIRED")


@dataclass(frozen=True)
class Facility:
    """One credit line to one lender."""

    facility_id: uuid.UUID
    lender_id: uuid.UUID
    code: str
    name: str
    credit_limit_cents: int
    advance_rate_bps: int
    status: FacilityStatus
    opened_on: date

    def __post_init__(self) -> None:
        """Code shape and numeric ranges of SPEC 12.1."""
        if not _FACILITY_CODE.fullmatch(self.code):
            raise InvalidValueError("facility_code", "PATTERN_MISMATCH")
        if not 0 <= self.credit_limit_cents <= MAX_CREDIT_LIMIT_CENTS:
            raise InvalidValueError("credit_limit_cents", "OUT_OF_RANGE")
        if not 0 <= self.advance_rate_bps <= MAX_ADVANCE_RATE_BPS:
            raise InvalidValueError("advance_rate_bps", "OUT_OF_RANGE")
        if not EARLIEST_DATE <= self.opened_on <= LATEST_DATE:  # SPEC_DECISIONS 100
            raise InvalidValueError("opened_on", "OUT_OF_RANGE")


@dataclass(frozen=True)
class FacilityWithLender:
    """A facility together with its lender, as the pipeline stages need them."""

    facility: Facility
    lender: Lender


@dataclass(frozen=True, repr=False)
class User:
    """A bank user. The password hash never appears in a repr."""

    user_id: uuid.UUID
    email: str
    display_name: str
    role: Role
    active: bool
    token_version: int

    def __post_init__(self) -> None:
        """Emails are stored lower case."""
        if self.email != self.email.lower():
            raise InvalidValueError("email", "PATTERN_MISMATCH")

    def __repr__(self) -> str:
        """Identifier and role only."""
        return f"User(user_id={self.user_id}, role={self.role.value})"


@dataclass(frozen=True)
class RegistryRequest:
    """One planned request to the registry, with the stored response once answered."""

    request_id: uuid.UUID
    submission_id: uuid.UUID
    origin: RequestOrigin
    batch_index: int
    idempotency_key: Sha256Hex
    kind: TapeKind
    key_version: KeyVersion
    item_count: int
    attempts: int
    response_status: int | None
    certificate_id: uuid.UUID | None
    registry_submission_id: uuid.UUID | None = None
    as_of_seq: int | None = None

    def __post_init__(self) -> None:
        """At most 500 items per request (the contract maximum)."""
        if not 1 <= self.item_count <= MAX_ITEMS_PER_REQUEST:
            raise InvalidValueError("item_count", "OUT_OF_RANGE")

    @property
    def answered(self) -> bool:
        """A response is stored; the request is never sent again."""
        return self.response_status is not None


@dataclass(frozen=True)
class Certificate:
    """A signed registry certificate as received, with the bank's own verification result (SPEC 15.2).

    `certificate_row_id` identifies the stored row: a certificate that failed is kept as
    evidence, and a verified one fetched later for the same request is a new row
    (SPEC_DECISIONS 61). `body_hash` is the hash the registry claimed;
    `delivered_body_sha256` is the hash the bank computed over the canonical body it
    received (SPEC_DECISIONS 64). They differ exactly when the body was altered.
    """

    certificate_row_id: uuid.UUID
    certificate_id: uuid.UUID
    request_id: uuid.UUID
    registry_submission_id: uuid.UUID
    kind: TapeKind
    as_of_seq: int
    issued_at: StorableTimestamp
    body: Mapping[str, JsonValue]
    body_hash: Sha256Hex
    signature: str
    signing_key_id: str
    failure: CertificateFailure | None
    verified_at: datetime
    received_at: datetime
    delivered_body_sha256: Sha256Hex

    @property
    def verified(self) -> bool:
        """True when every verification step passed."""
        return self.failure is None

    def as_received(self) -> SignedCertificate:
        """The stored body, hash, signature and key id, to verify again."""
        return SignedCertificate(
            self.body, self.body_hash.value, self.signature, self.signing_key_id, self.issued_at
        )


@dataclass(frozen=True)
class Checkpoint:
    """A signed registry checkpoint as received, with the body exactly as signed and the bank's verdict."""

    checkpoint_id: uuid.UUID
    checkpoint_seq: int
    last_event_seq: int
    head_hash: Sha256Hex
    signed_at: StorableTimestamp
    signature: str
    signing_key_id: str
    body: Mapping[str, JsonValue]
    received_at: datetime
    verified_at: datetime
    failure: CheckpointFailure | None
    first_bad_event_seq: int | None

    @property
    def verified(self) -> bool:
        """True when every verification step passed."""
        return self.failure is None
