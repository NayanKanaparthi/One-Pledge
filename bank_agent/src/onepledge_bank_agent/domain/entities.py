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
from onepledge_bank_agent.domain.values import KeyVersion, Lei, Sha256Hex

_FACILITY_CODE = re.compile(r"^[A-Z0-9-]{3,40}$")
MAX_ADVANCE_RATE_BPS = 10_000
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
            raise InvalidValueError("legal_name", "EMPTY")


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
        if self.credit_limit_cents < 0:
            raise InvalidValueError("credit_limit_cents", "OUT_OF_RANGE")
        if not 0 <= self.advance_rate_bps <= MAX_ADVANCE_RATE_BPS:
            raise InvalidValueError("advance_rate_bps", "OUT_OF_RANGE")


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
            raise InvalidValueError("email", "NOT_LOWER_CASE")

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
    """A signed registry certificate as received, with the bank's own verification result."""

    certificate_id: uuid.UUID
    request_id: uuid.UUID
    as_of_seq: int
    body: Mapping[str, JsonValue]
    body_hash: Sha256Hex
    signature: str
    signing_key_id: str
    failure: CertificateFailure | None
    verified_at: datetime

    @property
    def verified(self) -> bool:
        """True when every verification step passed."""
        return self.failure is None


@dataclass(frozen=True)
class Checkpoint:
    """A signed registry checkpoint as received, with the bank's own verification result."""

    checkpoint_seq: int
    last_event_seq: int
    head_hash: Sha256Hex
    signed_at: datetime
    signature: str
    signing_key_id: str
    failure: CheckpointFailure | None
    first_bad_event_seq: int | None

    @property
    def verified(self) -> bool:
        """True when every verification step passed."""
        return self.failure is None
