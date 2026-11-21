"""Entities without a state machine (SPEC 8.2) and the error catalogue (SPEC 4, 18.3)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from onepledge_bank_agent.domain import KeyVersion, Lei, Sha256Hex
from onepledge_bank_agent.domain.entities import (
    Certificate,
    Checkpoint,
    Facility,
    Lender,
    RegistryRequest,
    User,
)
from onepledge_bank_agent.domain.enums import (
    CertificateFailure,
    CheckpointFailure,
    FacilityStatus,
    RequestOrigin,
    Role,
    TapeKind,
)
from onepledge_bank_agent.domain.errors import (
    MESSAGES,
    DomainError,
    IllegalTransitionError,
    InvalidValueError,
    TapeDuplicateError,
)

pytestmark = pytest.mark.unit

AT = datetime(2026, 11, 20, tzinfo=UTC)


BASE = Facility(
    uuid.uuid4(), uuid.uuid4(), "HARBOR-SUMMIT", "line", 0, 10_000, FacilityStatus.ACTIVE, date(2025, 3, 1)
)


def test_reference_entities_validate_their_fields() -> None:
    assert BASE.code == "HARBOR-SUMMIT"
    with pytest.raises(InvalidValueError):
        replace(BASE, code="harbor")
    with pytest.raises(InvalidValueError):
        replace(BASE, credit_limit_cents=-1)
    with pytest.raises(InvalidValueError):
        replace(BASE, advance_rate_bps=10_001)
    with pytest.raises(InvalidValueError):
        replace(BASE, advance_rate_bps=-1)
    with pytest.raises(InvalidValueError):
        Lender(uuid.uuid4(), "  ", Lei("00000ONEPLEDGESUM134"))


def test_a_user_keeps_a_lower_case_email_and_a_short_repr() -> None:
    user = User(uuid.uuid4(), "analyst@harbor.example", "Ann", Role.ANALYST, True, 0)
    assert "analyst@" not in repr(user) and "ANALYST" in repr(user)
    with pytest.raises(InvalidValueError):
        User(uuid.uuid4(), "Analyst@harbor.example", "Ann", Role.ANALYST, True, 0)


def test_registry_proofs_report_whether_they_verified() -> None:
    request = RegistryRequest(
        uuid.uuid4(),
        uuid.uuid4(),
        RequestOrigin.TAPE,
        0,
        Sha256Hex("a" * 64),
        TapeKind.PLEDGE,
        KeyVersion(1, 1),
        500,
        0,
        None,
        None,
    )
    assert not request.answered and replace(request, response_status=201).answered
    with pytest.raises(InvalidValueError):
        replace(request, item_count=501)
    certificate = Certificate(uuid.uuid4(), uuid.uuid4(), 7, {}, Sha256Hex("b" * 64), "sig", "key", None, AT)
    assert certificate.verified
    assert not replace(certificate, failure=CertificateFailure.BAD_SIGNATURE).verified
    checkpoint = Checkpoint(3, 40, Sha256Hex("c" * 64), AT, "sig", "key", CheckpointFailure.FORK, 12)
    assert not checkpoint.verified and replace(checkpoint, failure=None).verified


def test_errors_carry_codes_and_fixed_messages_but_no_values() -> None:
    existing = uuid.uuid4()
    duplicate = TapeDuplicateError(existing)
    assert duplicate.message == "This file was already uploaded for this facility and kind."
    assert [(d.field, d.issue) for d in duplicate.details] == [("submission_id", str(existing))]
    assert IllegalTransitionError("loan", "LOCKED", "PENDING").message == MESSAGES["INTERNAL_ERROR"]
    assert DomainError().code == "INTERNAL_ERROR"
    assert all(message.endswith(".") and "{" not in message for message in MESSAGES.values())
