"""Entities without a state machine (SPEC 8.2) and the error catalogue (SPEC 4, 18.3)."""

from __future__ import annotations

import ast
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

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
    InvalidVinError,
    TapeDuplicateError,
)
from onepledge_bank_agent.domain.storable import StorableTimestamp

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
    assert replace(BASE, credit_limit_cents=2**63 - 1).credit_limit_cents == 2**63 - 1
    with pytest.raises(InvalidValueError):
        replace(BASE, credit_limit_cents=2**63)  # does not fit the bigint column
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
    certificate = Certificate(
        certificate_row_id=uuid.uuid4(),
        certificate_id=uuid.uuid4(),
        request_id=request.request_id,
        registry_submission_id=uuid.uuid4(),
        kind=TapeKind.PLEDGE,
        as_of_seq=7,
        issued_at=StorableTimestamp(AT),
        body={},
        body_hash=Sha256Hex("b" * 64),
        delivered_body_sha256=Sha256Hex("b" * 64),
        signature="sig",
        signing_key_id="key",
        failure=None,
        verified_at=AT,
        received_at=AT,
    )
    assert certificate.verified
    assert not replace(certificate, failure=CertificateFailure.BAD_SIGNATURE).verified
    checkpoint = Checkpoint(
        checkpoint_id=uuid.uuid4(),
        checkpoint_seq=3,
        last_event_seq=40,
        head_hash=Sha256Hex("c" * 64),
        signed_at=StorableTimestamp(AT),
        signature="sig",
        signing_key_id="key",
        body={},
        received_at=AT,
        verified_at=AT,
        failure=CheckpointFailure.FORK,
        first_bad_event_seq=12,
    )
    assert not checkpoint.verified and replace(checkpoint, failure=None).verified


def test_errors_carry_codes_and_fixed_messages_but_no_values() -> None:
    existing = uuid.uuid4()
    duplicate = TapeDuplicateError(existing)
    assert duplicate.message == "This file was already uploaded for this facility and kind."
    assert [(d.field, d.issue) for d in duplicate.details] == [("submission_id", str(existing))]
    assert IllegalTransitionError("loan", "LOCKED", "PENDING").message == MESSAGES["INTERNAL_ERROR"]
    assert DomainError().code == "INTERNAL_ERROR"
    assert all(message.endswith(".") and "{" not in message for message in MESSAGES.values())


VOCABULARY = frozenset(
    {
        "REQUIRED", "INVALID_TYPE", "INVALID_INTEGER", "OUT_OF_RANGE", "PATTERN_MISMATCH", "INVALID_VALUE",
        "INVALID_DATE", "TOO_LONG", "TOO_SHORT", "TOO_MANY_ITEMS", "TOO_FEW_ITEMS", "DUPLICATE",
        "UNKNOWN_FIELD", "UNKNOWN_PARAMETER",
    }
)  # fmt: skip


def test_every_issue_code_the_domain_raises_is_in_the_shared_vocabulary() -> None:
    """SPEC_DECISIONS 22 finding 7: one vocabulary for `details[].issue`, read from the source itself."""
    source = Path(__file__).resolve().parents[2] / "src" / "onepledge_bank_agent"
    raised: set[str] = set()
    for path in source.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"InvalidValueError", "NonCanonicalValueError", "ErrorDetail"}
                and len(node.args) == 2
                and isinstance(node.args[1], ast.Constant)
            ):
                raised.add(str(node.args[1].value))
    assert raised and raised <= VOCABULARY, raised - VOCABULARY
    assert [d.issue for d in InvalidVinError("CHECK_DIGIT").details] == ["INVALID_VALUE"]
    assert [d.issue for d in InvalidVinError("LENGTH").details] == ["PATTERN_MISMATCH"]
    assert InvalidVinError("LENGTH").reason == "LENGTH"
