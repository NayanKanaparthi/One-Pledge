"""Stored records built from the domain's own types, for the shared repository port tests.

Every builder returns a value that is legal for both implementations of the ports: the
in-memory one and PostgreSQL with its guards, checks and foreign keys.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import TypedDict, Unpack

from onepledge_bank_agent.application.evidence_ports import IntegrityRun, PlannedItem
from onepledge_bank_agent.application.ports import NewItem
from onepledge_bank_agent.domain.alert import Alert, AlertIdentity, MatchFacts
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.certificate_rules import delivered_sha256
from onepledge_bank_agent.domain.entities import Certificate, Checkpoint, RegistryRequest
from onepledge_bank_agent.domain.enums import (
    AlertRole,
    CertificateFailure,
    CheckOutcome,
    CheckpointFailure,
    CheckType,
    ExplanationSource,
    IntegrityRunStatus,
    MatchBasis,
    MatchKind,
    QuarantineReason,
    RegistryItemResult,
    RequestOrigin,
    RowDisposition,
    TapeKind,
    TokenKind,
)
from onepledge_bank_agent.domain.loan import Loan, LoanDraft
from onepledge_bank_agent.domain.registry_records import (
    ItemOutcome,
    QuarantinedMessage,
    QuarantinedPayload,
    RegistryAnswer,
)
from onepledge_bank_agent.domain.registry_results import StoredResult
from onepledge_bank_agent.domain.tape_file import RawRow, raw_payload, row_sha256
from onepledge_bank_agent.domain.values import (
    ALLOWED_CHECK_RESULTS,
    BorrowerRef,
    CheckResult,
    KeyVersion,
    LenderLoanNumber,
    LoanTerms,
    Money,
    Sha256Hex,
    Token,
    lei_checksum,
)
from onepledge_bank_agent.domain.vin import Vin

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
KEY_V1 = KeyVersion(1, 1)
KEY_V2 = KeyVersion(2, 1)


def at(minutes: float) -> datetime:
    """`minutes` after the tests' reference time."""
    return T0 + timedelta(minutes=minutes)


def hex64(seed: object) -> Sha256Hex:
    """A distinct SHA-256 value for any seed."""
    return Sha256Hex(hashlib.sha256(str(seed).encode()).hexdigest())


def lei(stem: str) -> str:
    """An 18 character stem completed with the two check digits that make it a valid LEI."""
    return next(f"{stem}{d:02d}" for d in range(100) if lei_checksum(f"{stem}{d:02d}") == 1)


def raw_rows(header: Sequence[str], count: int, start: int = 1) -> list[RawRow]:
    """`count` raw rows numbered from `start`, each cell naming its row and column."""
    rows = []
    for number in range(start, start + count):
        cells = tuple(f"r{number}c{index}" for index in range(len(header)))
        text = ",".join(cells)
        rows.append(RawRow(number, text, cells, raw_payload(header, cells), row_sha256(text)))
    return rows


def item(
    raw_id: uuid.UUID, row_number: int, disposition: RowDisposition, loan_id: uuid.UUID | None
) -> NewItem:
    """One submission item without cleaning issues."""
    return NewItem(uuid.uuid4(), raw_id, row_number, disposition, loan_id, ())


def loan(
    facility_id: uuid.UUID, submission_id: uuid.UUID, vin: str, created: datetime = T0, balance: int = 900_00
) -> Loan:
    """A PENDING loan of the facility first seen on the submission."""
    terms = LoanTerms(Vin(vin), BorrowerRef("C1234567"), date(2025, 1, 2), Money(1_000_00))
    draft = LoanDraft(
        uuid.uuid4(), facility_id, submission_id, LenderLoanNumber("SAF-1"), terms, Money(balance)
    )
    return Loan.open(draft, created)[0]


def check_results(outcome: CheckOutcome = CheckOutcome.PASS) -> list[CheckResult]:
    """The four check results of one loan, each with the first reason allowed for it and evidence."""
    return [
        CheckResult(
            kind,
            outcome,
            next(r for o, r in sorted(ALLOWED_CHECK_RESULTS[kind]) if o is outcome),
            {"checked": kind.value},
        )
        for kind in CheckType
    ]


def tokens(
    seed: object, version: KeyVersion = KEY_V1, kinds: Sequence[TokenKind] = tuple(TokenKind)
) -> list[Token]:
    """One token per kind, distinct for each seed, kind and version."""
    return [Token(kind, hex64((seed, kind, str(version))), version) for kind in kinds]


def request(
    submission_id: uuid.UUID,
    batch_index: int = 0,
    item_count: int = 1,
    origin: RequestOrigin = RequestOrigin.TAPE,
    kind: TapeKind = TapeKind.PLEDGE,
) -> RegistryRequest:
    """A planned registry request, never sent."""
    return RegistryRequest(
        uuid.uuid4(), submission_id, origin, batch_index, hex64(uuid.uuid4()), kind, KEY_V1, item_count,
        0, None, None, None, None,
    )  # fmt: skip


def planned(loan_ids: Sequence[uuid.UUID]) -> list[PlannedItem]:
    """One planned item per loan, in the given order."""
    return [PlannedItem(uuid.uuid4(), position, loan_id) for position, loan_id in enumerate(loan_ids)]


def outcome(
    item_ref: uuid.UUID, result: RegistryItemResult, case_id: uuid.UUID | None = None
) -> tuple[ItemOutcome, StoredResult]:
    """A registry item outcome and the result the bank stores for it."""
    conflict = result is RegistryItemResult.CONFLICT
    found = ItemOutcome(
        item_ref, hex64(item_ref).value, result.value,
        uuid.uuid4() if result is RegistryItemResult.LOCKED else None, conflict,
        MatchKind.EXACT if conflict else None, MatchBasis.ASSET if conflict else None,
        (TokenKind.ASSET,) if conflict else (), case_id, None,
    )  # fmt: skip
    return found, StoredResult(result, None)


def answer(
    planned_request: RegistryRequest, outcomes: Sequence[ItemOutcome], as_of_seq: int = 7
) -> RegistryAnswer:
    """A 201 answer to the request carrying these outcomes."""
    body: Mapping[str, JsonValue] = {"as_of_seq": as_of_seq, "items": len(outcomes)}
    return RegistryAnswer(
        201, False, uuid.uuid4(), uuid.uuid4(), planned_request.kind, planned_request.key_version, as_of_seq,
        uuid.uuid4(), tuple(outcomes), body,
    )  # fmt: skip


class CertificateChanges(TypedDict, total=False):
    """The fields of a received certificate a port test varies."""

    failure: CertificateFailure | None
    body: Mapping[str, JsonValue]
    signature: str
    received: datetime


def certificate(
    request_id: uuid.UUID, certificate_id: uuid.UUID | None = None, **changes: Unpack[CertificateChanges]
) -> Certificate:
    """A received certificate with the bank's verdict; the delivered hash is computed from the body."""
    failure = changes.get("failure")
    signature = changes.get("signature", "c2lnbmF0dXJl")
    received = changes.get("received", T0)
    delivered = changes.get("body", {"certificate": "genuine"})
    digest = delivered_sha256(delivered)
    assert digest is not None, "a canonical body"
    return Certificate(
        uuid.uuid4(), certificate_id or uuid.uuid4(), request_id, uuid.uuid4(), TapeKind.PLEDGE, 7, T0,
        delivered, hex64("claimed"), signature, "registry-1", failure, received, received, Sha256Hex(digest),
    )  # fmt: skip


def checkpoint(
    seq: int,
    *,
    failure: CheckpointFailure | None = None,
    head: str = "head",
    signature: str = "c2lnbmF0dXJl",
    received: datetime = T0,
) -> Checkpoint:
    """A received checkpoint with the bank's verdict."""
    return Checkpoint(
        uuid.uuid4(), seq, seq * 10, hex64((seq, head)), T0, signature, "registry-1", {"checkpoint_seq": seq},
        received, received, failure, 3 if failure is CheckpointFailure.CHAIN_BREAK else None,
    )  # fmt: skip


def quarantined(
    message_id: uuid.UUID | None = None,
    message_type: str = "ALERT",
    reason: QuarantineReason = QuarantineReason.SCHEMA_MISMATCH,
    received: datetime = T0,
) -> QuarantinedMessage:
    """A quarantined outbox message; an OVERSIZED one keeps no payload (it is withheld)."""
    oversized = reason is QuarantineReason.OVERSIZED
    payload: JsonValue = None if oversized else {"unexpected": True}
    held = QuarantinedPayload(reason, payload, hex64(message_type).value, withheld=oversized)
    return QuarantinedMessage(uuid.uuid4(), message_id or uuid.uuid4(), 4, message_type, held, received)


def alert(loan_id: uuid.UUID, case_id: uuid.UUID | None = None, received: datetime = T0) -> Alert:
    """A NEW alert on an OPEN case: the bank holds the loan the registry matched exactly."""
    identity = AlertIdentity(uuid.uuid4(), case_id or uuid.uuid4(), loan_id)
    match = MatchFacts(AlertRole.HOLDER, MatchKind.EXACT, MatchBasis.ASSET, (TokenKind.ASSET,))
    return Alert.raise_new(
        identity, match, ("Another member pledged this car.", ExplanationSource.TEMPLATE), received
    )


def integrity_run(
    ran: datetime, triggered_by: uuid.UUID | None = None, status: IntegrityRunStatus = IntegrityRunStatus.OK
) -> IntegrityRun:
    """A deep verification with a small report."""
    return IntegrityRun(uuid.uuid4(), ran, triggered_by, status, {"status": status.value})
