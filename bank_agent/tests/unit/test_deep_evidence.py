"""Deep verification covers every decision the bank acts on, in memory (SPEC_DECISIONS 107).

The memory unit of work keeps what the tables keep, so each edit below is the edit a superuser
makes on PostgreSQL (the integration tests make them there). A clean bank verifies with no
evidence problem; each edit is then found and located: the stage digests by table, submission
and stage, the re-derivation by row or loan, the replay by loan, tape, alert or user.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from bank_testkit.memory.bank import MemoryBank, vins
from onepledge_bank_agent.application.deep_evidence import reference_digests, request_digest, stage_digests
from onepledge_bank_agent.domain.audit import Actor, AuditDraft, AuditEntry
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    AuditObjectType,
    CheckOutcome,
    LoanState,
    TapeStatus,
    TokenKind,
)
from onepledge_bank_agent.domain.integrity import EvidenceProblemKind
from onepledge_bank_agent.domain.replay import (
    IllegalStepError,
    State,
    alert_step,
    loan_step,
    tape_step,
    user_step,
)
from onepledge_bank_agent.domain.stage_digests import body_sha256, cell, row, rows_sha256
from onepledge_bank_agent.domain.values import Money, Sha256Hex, Token

pytestmark = pytest.mark.unit

DIGEST = EvidenceProblemKind.DIGEST_MISMATCH
DERIVED = EvidenceProblemKind.DERIVATION_MISMATCH
STATE = EvidenceProblemKind.STATE_MISMATCH


@pytest.fixture
def bank() -> MemoryBank:
    bank = MemoryBank()
    bank.pledge(vins(3))
    assert bank.verify().evidence.problems == (), "a clean bank first"
    return bank


def found(bank: MemoryBank) -> set[tuple[str, str | None, EvidenceProblemKind, TapeStatus | None]]:
    return {(p.table, p.row_id, p.problem, p.stage) for p in bank.verify().evidence.problems}


def only_tape(bank: MemoryBank) -> uuid.UUID:
    (submission,) = bank.world.store.state.tapes
    return submission


def test_every_stage_entry_carries_the_digests_of_what_it_produced(bank: MemoryBank) -> None:
    submission = only_tape(bank)
    entries = {e.detail["to"]: e.detail for e in bank.world.audit(AuditAction.TAPE_STAGE_ADVANCED)}
    with bank.world.units() as unit:
        for status in (TapeStatus.CLEANED, TapeStatus.CHECKED, TapeStatus.SUBMITTED):
            digests = stage_digests(unit.history, submission, status)
            assert digests and {key: entries[status.value][key] for key in digests} == digests, status
        assert stage_digests(unit.history, submission, TapeStatus.TOKENIZED) == {}


def test_a_flipped_check_is_a_digest_mismatch_of_the_check_stage(bank: MemoryBank) -> None:
    submission = only_tape(bank)
    state = bank.world.store.state
    key = next(k for k in state.checks if k[0] == submission)
    first, *rest = state.checks[key]
    state.checks[key] = [replace(first, evidence={"edited": True}), *rest]
    assert ("local_checks", str(submission), DIGEST, TapeStatus.CHECKED) in found(bank)


def test_a_changed_token_is_a_digest_mismatch_of_the_submit_stage(bank: MemoryBank) -> None:
    submission = only_tape(bank)
    state = bank.world.store.state
    loan_id = next(iter(state.tokens))
    token = state.tokens[loan_id][0]
    state.tokens[loan_id] = [
        Token(token.kind, Sha256Hex("e" * 64), token.key_version),
        *state.tokens[loan_id][1:],
    ]
    assert ("loan_tokens", str(submission), DIGEST, TapeStatus.SUBMITTED) in found(bank)


def test_a_removed_issue_is_a_digest_and_a_derivation_mismatch() -> None:
    """A VIN in lower case is fixed by cleaning (VIN_CASE_NORMALISED); the stored issue is then removed."""
    bank = MemoryBank()
    bank.pledge([vin.lower() for vin in vins(1, start=50)])
    assert bank.verify().evidence.problems == ()
    state = bank.world.store.state
    submission = only_tape(bank)
    items = state.items[submission]
    index, item = next((i, item) for i, item in enumerate(items) if item.issues)
    state.items[submission] = [*items[:index], replace(item, issues=item.issues[1:]), *items[index + 1 :]]
    problems = found(bank)
    assert ("cleaning_issues", str(submission), DIGEST, TapeStatus.CLEANED) in problems
    assert ("cleaning_issues", f"{submission}/{item.row_number}", DERIVED, None) in problems


def test_a_changed_balance_is_a_derivation_mismatch_of_the_loan(bank: MemoryBank) -> None:
    state = bank.world.store.state
    loan_id, loan = next(iter(state.loans.items()))
    record = loan.record
    edited = replace(record, current_balance=Money(record.current_balance.cents + 1))
    object.__setattr__(loan, "_record", edited)
    assert ("loans", str(loan_id), DERIVED, None) in found(bank)


def test_a_loan_state_its_entries_do_not_lead_to_is_a_state_mismatch(bank: MemoryBank) -> None:
    state = bank.world.store.state
    loan_id, loan = next(iter(state.loans.items()))
    object.__setattr__(loan, "_record", replace(loan.record, state=LoanState.RELEASED))
    assert ("loans", str(loan_id), STATE, None) in found(bank)


def test_a_changed_request_answer_is_a_digest_mismatch_of_the_submit_stage(bank: MemoryBank) -> None:
    submission = only_tape(bank)
    state = bank.world.store.state
    request_id = next(iter(state.requests))
    state.requests[request_id] = replace(state.requests[request_id], as_of_seq=999)
    assert ("registry_requests", str(submission), DIGEST, TapeStatus.SUBMITTED) in found(bank)


def test_changed_reference_data_is_a_digest_mismatch_of_its_table(bank: MemoryBank) -> None:
    with bank.world.units() as unit:
        detail = {"via": "test", **reference_digests(unit.history)}
        seeded = AuditDraft(AuditAction.REFERENCE_DATA_SEEDED, Actor.system(), AuditObjectType.REFERENCE_DATA,
                            None, detail=detail)  # fmt: skip
        unit.record(seeded)
        unit.commit()
    assert found(bank) == set(), "the digests of the tables as seeded"
    state = bank.world.store.state  # each commit replaces the state
    facility_id = next(iter(state.facilities))
    state.facilities[facility_id] = replace(state.facilities[facility_id], name="Renamed")
    problems = found(bank)
    assert ("facilities", None, DIGEST, None) in problems and ("lenders", None, DIGEST, None) not in problems


def test_a_resubmitted_request_carries_its_digest(bank: MemoryBank) -> None:
    request_id = next(iter(bank.world.store.state.requests))
    with bank.world.units() as unit:
        (key,) = request_digest(unit.history, request_id)
    assert key == "requests_sha256"


def entry(action: AuditAction, seq: int, **detail: object) -> AuditEntry:
    return AuditEntry(
        uuid.uuid4(), seq, datetime(2026, 11, 1, tzinfo=UTC), Actor.system().actor_type, None, action.value,
        "LOAN", uuid.uuid4(), "SUCCESS", None, {k: cell(v) for k, v in detail.items()}, "0" * 64, "1" * 64,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("step", "state", "item"),
    [
        (loan_step, None, entry(AuditAction.LOAN_STATE_CHANGED, 1, to="LOCKED")),
        (loan_step, ("PENDING",), entry(AuditAction.LOAN_STATE_CHANGED, 2, **{"from": "HELD_LOCAL"})),
        (tape_step, ("RECEIVED",), entry(AuditAction.TAPE_UPLOADED, 2)),
        (tape_step, None, entry(AuditAction.TAPE_STAGE_ADVANCED, 1, to="CLEANED")),
        (tape_step, ("RECEIVED",), entry(AuditAction.TAPE_STAGE_ADVANCED, 2, to="CHECKED")),
        (tape_step, ("COMPLETED",), entry(AuditAction.TAPE_FAILED, 2)),
        (tape_step, ("CLEANED",), entry(AuditAction.TAPE_RETRY_REQUESTED, 2, resume_status="CLEANED")),
        (alert_step, ("NEW", "OPEN"), entry(AuditAction.ALERT_RECEIVED, 2, status="NEW", case_status="OPEN")),
        (alert_step, None, entry(AuditAction.ALERT_RECEIVED, 1, status="NEW")),
        (user_step, None, entry(AuditAction.USER_UPDATED, 1, role="ANALYST", active=True)),
        (user_step, None, entry(AuditAction.USER_CREATED, 1, role="ANALYST", active="yes")),
    ],
    ids=[
        "a loan created LOCKED", "a from that is not the current state", "a tape uploaded twice",
        "a stage before the upload", "a stage skipped", "a completed tape failing",
        "a retry while not failed",
        "an alert received twice", "an alert without its case status", "a user updated before created",
        "active that is not a boolean",
    ],
)  # fmt: skip
def test_an_entry_that_cannot_follow_the_state_replayed_so_far_is_refused(
    step: object, state: State | None, item: AuditEntry
) -> None:
    assert callable(step)
    with pytest.raises(IllegalStepError):
        step(state, item)


def test_the_cells_of_a_digest_row_are_canonical() -> None:
    assert row(uuid.UUID(int=1), TokenKind.ASSET, datetime(2026, 1, 1, tzinfo=UTC), (CheckOutcome.PASS,)) == (
        "00000000-0000-0000-0000-000000000001", "ASSET", "2026-01-01T00:00:00Z", ["PASS"],
    )  # fmt: skip
    assert body_sha256(None) is None
    assert body_sha256({"a": 1}) == hashlib.sha256(b'{"a":1}').hexdigest()
    assert rows_sha256([row(1)]) == hashlib.sha256(b"[[1]]").hexdigest() != rows_sha256([row(2)])
