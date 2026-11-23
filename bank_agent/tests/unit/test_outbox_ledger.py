"""The outbox can neither block nor lose a message (SPEC_DECISIONS 101; review 3, F2 and F3).

Each test states what is stored, audited and acknowledged, that the next message is applied,
whether the sync was complete, and what that does to the halt rule: an element without a
usable id is kept once by its hash and leaves the bank blind; an unusable type is poison; a
deferral is never turned into poison and a deferred CHECKPOINT holds back only later
checkpoints; an acknowledgement refused is audited once and a redelivery is never handled
twice; a CASE_UPDATE that arrives before its ALERT is kept and applied late.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from bank_testkit.memory.bank import MemoryBank, vins
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.dependency_errors import (
    RegistryProtocolError,
    RegistryRejectedError,
    RegistryUnavailableError,
)
from onepledge_bank_agent.domain.enums import (
    AlertStatus,
    AuditAction,
    CaseStatus,
    FailureReason,
    QuarantineReason,
    TapeStatus,
    TokenKind,
)
from onepledge_bank_agent.domain.integrity import SectionStatus
from onepledge_bank_agent.domain.outbox import AckStatus, OutboxOutcome
from onepledge_bank_agent.domain.registry_records import OutboxMessage, UnacknowledgeableElement
from onepledge_bank_agent.domain.store_errors import StoreRefusedError

pytestmark = pytest.mark.unit

AT = "2026-11-22T00:00:00Z"
LIMIT = 300.0


def asset_of(bank: MemoryBank, vin: str) -> str:
    loan = bank.loans()[vin]
    return next(
        t.value.value for t in bank.world.store.state.tokens[loan.loan_id] if t.kind is TokenKind.ASSET
    )


def pledged(bank: MemoryBank) -> str:
    """A LOCKED loan's asset code, after one complete sync."""
    (car,) = vins(1, start=60)
    bank.pledge([car])
    assert bank.outbox_sync().sync().complete
    return asset_of(bank, car)


def ledger(bank: MemoryBank, message_id: uuid.UUID) -> tuple[OutboxOutcome | None, AckStatus]:
    entry = bank.world.store.state.outbox_ledger[message_id]
    return entry.outcome, entry.ack_status


def synced_at(bank: MemoryBank) -> datetime | None:
    return bank.world.store.state.outbox_state.last_complete_sync_at


def audited(bank: MemoryBank, action: AuditAction) -> int:
    return len(bank.world.audit(action))


def submits(bank: MemoryBank) -> TapeStatus | str:
    """A tape uploaded now: COMPLETED, or the failure code it stopped on at submit."""
    tape = bank.upload(vins(1, start=80 + len(bank.world.store.state.tapes)))
    last = bank.run(tape)[-1]
    if last.failure is not None:
        assert bank.tape(tape).progress.resume_status is TapeStatus.TOKENIZED, "every stage before submit ran"
        return str(last.failure["failure_code"])
    return last.status


UNUSABLE_TYPES: list[JsonValue] = [None, 7, "", "A" * 33, "A" * 65, "ALERT\x00", "alert", "VIN-1HGCM"]


@pytest.mark.parametrize("kind", UNUSABLE_TYPES, ids=repr)
def test_a_message_with_a_usable_id_and_an_unusable_type_is_poison_recorded_as_unreadable(
    kind: JsonValue,
) -> None:
    """Review 3 F2: `type` null, 7, empty, too long, holding U+0000 blocked the outbox for good."""
    bank = MemoryBank()
    token = pledged(bank)
    poison = bank.registry.deliver_wire({"type": kind, "created_at": AT, "payload": {"note": "x"}})
    assert isinstance(poison, OutboxMessage)
    following = bank.registry.alert(token)
    result = bank.outbox_sync().sync()
    assert result.complete and result.messages_processed == 2
    (held,) = bank.world.store.state.quarantined
    assert (held.message_id, held.message_type) == (poison.message_id, "UNREADABLE")
    assert held.quarantined.reason is QuarantineReason.SCHEMA_MISMATCH
    assert ledger(bank, poison.message_id) == (OutboxOutcome.QUARANTINED, AckStatus.ACKED)
    assert audited(bank, AuditAction.REGISTRY_MESSAGE_QUARANTINED) == 1
    assert bank.registry.pending() == [], "acknowledged"
    assert following in {a.record.registry_case_id for a in bank.alerts()}, "the next message was applied"
    assert bank.report().registry_messages.status is SectionStatus.BROKEN
    assert submits(bank) is TapeStatus.COMPLETED, "only a quarantined CHECKPOINT halts for good"


UNACKNOWLEDGEABLE: list[JsonValue] = [
    {"type": "ALERT", "position": 1, "created_at": AT, "payload": {}},
    {"message_id": "not-a-uuid", "type": "ALERT", "position": 1, "created_at": AT, "payload": {}},
    {"message_id": "0F8FAD5B-D9CB-469F-A165-70867728950E", "type": "ALERT", "position": 1, "payload": {}},
    {"message_id": 7, "type": "ALERT"},
    "a string",
    None,
    [1, 2],
]


@pytest.mark.parametrize("element", UNACKNOWLEDGEABLE, ids=repr)
def test_an_element_without_a_usable_id_is_kept_once_by_its_hash_and_leaves_the_bank_blind(
    element: JsonValue,
) -> None:
    bank = MemoryBank()
    token = pledged(bank)
    parsed = bank.registry.deliver_element(element)
    assert isinstance(parsed, UnacknowledgeableElement)
    following = bank.registry.alert(token)
    first, second = bank.outbox_sync().sync(), bank.outbox_sync().sync()
    assert (first.complete, first.reason) == (False, "UNACKNOWLEDGEABLE")
    assert (second.complete, first.messages_processed) == (False, 1)
    (held,) = bank.world.store.state.quarantined
    assert (held.message_id, held.quarantined.reason) == (None, QuarantineReason.UNACKNOWLEDGEABLE)
    assert held.quarantined.payload_sha256 == parsed.evidence.payload_sha256
    assert audited(bank, AuditAction.REGISTRY_MESSAGE_QUARANTINED) == 1, "a redelivery adds nothing"
    assert following in {a.record.registry_case_id for a in bank.alerts()}, "processing went on"
    assert not bank.halt_rule().active(), "the last complete sync is recent"
    bank.world.clock.advance(LIMIT + 1)
    assert bank.halt_rule().active(), "blind once the limit passed"
    assert submits(bank) == "REGISTRY_INTEGRITY_BROKEN", "a blind bank sends nothing"
    blind = bank.report().registry_messages
    assert blind.status is SectionStatus.BROKEN


def test_a_page_that_cannot_be_read_makes_the_sync_incomplete_and_acknowledges_nothing() -> None:
    bank = MemoryBank()
    token = pledged(bank)
    case_id = bank.registry.alert(token)
    bank.registry.fail("outbox", RegistryProtocolError(200), times=2)
    result = bank.outbox_sync().sync()
    assert (result.complete, result.reason, result.messages_processed) == (False, "MALFORMED_RESPONSE", 0)
    assert bank.registry.calls["acknowledge"] == 0 and len(bank.registry.pending()) == 1
    bank.world.clock.advance(LIMIT + 1)
    assert not bank.outbox_sync().sync().complete
    assert audited(bank, AuditAction.REGISTRY_OUTBOX_BLIND) == 1
    assert bank.halt_rule().active()
    assert bank.outbox_sync().sync().complete, "the next readable page ends it"
    assert case_id in {a.record.registry_case_id for a in bank.alerts()}
    assert audited(bank, AuditAction.REGISTRY_OUTBOX_RECOVERED) == 1
    assert not bank.halt_rule().active()


def test_an_acknowledgement_the_registry_refuses_is_audited_once_and_never_handled_again() -> None:
    """Review 3 F2, related: a 404 to the acknowledgement made an unknown type audited on every sync."""
    bank = MemoryBank()
    pledged(bank)
    future = bank.registry.deliver("FUTURE_TYPE", None)
    refusal = RegistryRejectedError(404, FailureReason.REQUEST_REFUSED)
    bank.registry.fail("acknowledge", refusal, times=7)
    results = [bank.outbox_sync().sync() for _ in range(7)]
    assert {(r.complete, r.reason) for r in results} == {(False, "ACK_REFUSED")}
    assert audited(bank, AuditAction.REGISTRY_MESSAGE_IGNORED) == 1, "handled once"
    assert audited(bank, AuditAction.REGISTRY_ACK_REFUSED) == 1, "recorded once"
    assert ledger(bank, future.message_id) == (OutboxOutcome.IGNORED, AckStatus.REFUSED)
    assert bank.outbox_sync().sync().complete, "the registry took it in the end"
    assert ledger(bank, future.message_id) == (OutboxOutcome.IGNORED, AckStatus.ACKED)
    assert audited(bank, AuditAction.REGISTRY_MESSAGE_IGNORED) == 1


def test_an_acknowledgement_that_fails_for_a_passing_reason_is_tried_on_the_next_sync() -> None:
    bank = MemoryBank()
    token = pledged(bank)
    case_id = bank.registry.alert(token)
    (message,) = bank.registry.pending()
    assert isinstance(message, OutboxMessage)
    bank.registry.fail("acknowledge", RegistryUnavailableError(503, FailureReason.SERVER_ERROR), times=3)
    for _ in range(3):
        result = bank.outbox_sync().sync()
        assert (result.complete, result.reason) == (False, "ACK_PENDING")
        assert ledger(bank, message.message_id) == (OutboxOutcome.APPLIED, AckStatus.PENDING)
    assert bank.outbox_sync().sync().complete
    assert ledger(bank, message.message_id) == (OutboxOutcome.APPLIED, AckStatus.ACKED)
    assert [a.record.registry_case_id for a in bank.alerts()] == [case_id], "applied once"
    assert audited(bank, AuditAction.ALERT_RECEIVED) == 1
    assert audited(bank, AuditAction.REGISTRY_ACK_REFUSED) == 0


def test_a_checkpoint_deferred_by_an_outage_never_becomes_poison_and_does_not_hold_back_alerts() -> None:
    """`/v1/ledger/events` answers 503 for ten syncs with the outbox healthy."""
    bank = MemoryBank()
    token = pledged(bank)
    bank.registry.checkpoint()
    bank.registry.checkpoint()
    held_back = [m.message_id for m in bank.registry.pending() if isinstance(m, OutboxMessage)]
    outage = RegistryUnavailableError(503, FailureReason.SERVER_ERROR)
    bank.registry.fail("event_headers", outage, times=11)  # the eleventh: the catch-up before submit
    cases = []
    for sync in range(10):
        cases.append(bank.registry.alert(token))
        result = bank.outbox_sync().sync()
        assert (result.complete, result.reason) == (False, "REGISTRY_UNAVAILABLE"), sync
        assert {a.record.registry_case_id for a in bank.alerts()} >= set(cases), "alerts are delivered"
        bank.world.clock.advance(60)
    assert bank.world.store.state.quarantined == [], "a deferral is never poison"
    assert bank.world.store.state.checkpoints == []
    assert [ledger(bank, m)[0] for m in held_back if m in bank.world.store.state.outbox_ledger] == [None]
    assert bank.halt_rule().active(), "blind after the limit"
    assert audited(bank, AuditAction.REGISTRY_OUTBOX_BLIND) == 1, "audited once, not per poll"
    assert submits(bank) == "REGISTRY_INTEGRITY_BROKEN"
    result = bank.outbox_sync().sync()
    assert result.complete and result.checkpoints_verified == 2, "one good sync restores everything"
    assert not bank.halt_rule().active() and audited(bank, AuditAction.REGISTRY_OUTBOX_RECOVERED) == 1
    assert bank.report().registry_messages.status is SectionStatus.OK
    assert submits(bank) is TapeStatus.COMPLETED, "no permanent halt"


def test_a_case_update_that_matches_no_alert_is_kept_and_applied_when_its_alert_arrives() -> None:
    """Review 3 F3: the update was acknowledged and left no record."""
    bank = MemoryBank()
    token = pledged(bank)
    case_id = uuid.uuid4()
    bank.registry.cases[case_id] = token
    bank.registry.case_update(case_id, CaseStatus.CONFIRMED)
    (update,) = bank.registry.pending()
    assert isinstance(update, OutboxMessage)
    assert bank.outbox_sync().sync().complete
    (held,) = bank.world.store.state.quarantined
    assert (held.message_id, held.quarantined.reason) == (update.message_id, QuarantineReason.UNMATCHED)
    assert held.quarantined.payload is not None, "the payload is kept to be applied"
    (unmatched,) = bank.world.audit(AuditAction.CASE_UPDATE_UNMATCHED)
    assert unmatched.outcome.value == "FAILURE" and unmatched.object_id == held.quarantine_id
    assert bank.registry.pending() == [] and bank.report().registry_messages.status is SectionStatus.BROKEN
    bank.registry.deliver_wire(
        {"type": "ALERT", "created_at": AT, "payload": {
            "case_id": str(case_id), "role": "HOLDER", "asset_token": token, "match_kind": "EXACT",
            "match_basis": "ASSET", "kinds_matched": ["ASSET"], "opened_seq": 4}}
    )  # fmt: skip
    assert bank.outbox_sync().sync().complete
    (alert,) = [a for a in bank.alerts() if a.record.registry_case_id == case_id]
    assert (alert.status, alert.record.case_status) == (AlertStatus.ESCALATED, CaseStatus.CONFIRMED)
    (late,) = [
        d for d in bank.world.audit(AuditAction.ALERT_CASE_UPDATED) if d.object_id == alert.record.alert_id
    ]
    assert late.detail["from_quarantine_id"] == str(held.quarantine_id)
    assert ledger(bank, update.message_id) == (OutboxOutcome.APPLIED_LATE, AckStatus.ACKED)
    assert bank.report().registry_messages.status is SectionStatus.OK, "no open quarantined row"


def test_a_handler_write_the_store_refuses_is_poison_refused_by_database_with_its_sqlstate() -> None:
    """Decision 101: a database refusal is poison, never a deferral (bank quality finding 8)."""
    bank = MemoryBank()
    token = pledged(bank)
    refused = StoreRefusedError("23514", "23514", "ck_alerts_status")
    bank.world.faults.arm(FaultPoint.OUTBOX_HANDLER_BEFORE_COMMIT, error=refused)
    case_id = bank.registry.alert(token)
    following = bank.registry.alert(token)
    result = bank.outbox_sync().sync()
    assert result.complete and result.messages_processed == 2
    (held,) = bank.world.store.state.quarantined
    assert held.quarantined.reason is QuarantineReason.REFUSED_BY_DATABASE
    (entry,) = bank.world.audit(AuditAction.REGISTRY_MESSAGE_QUARANTINED)
    assert entry.detail["sqlstate"] == "23514"
    assert {a.record.registry_case_id for a in bank.alerts()} == {following}, "rolled back, then went on"
    assert case_id not in {a.record.registry_case_id for a in bank.alerts()}


def test_a_bank_that_never_completed_a_sync_reads_the_outbox_before_its_first_submission() -> None:
    bank = MemoryBank()
    (car,) = vins(1, start=70)
    tape = bank.upload([car])
    assert synced_at(bank) is None
    assert bank.halt_rule().active(), "never synced: blind"
    assert bank.run(tape)[-1].status is TapeStatus.COMPLETED, "the halt rule caught up first"
    assert synced_at(bank) == bank.world.clock.now()
    assert audited(bank, AuditAction.REGISTRY_OUTBOX_BLIND) == 0, "never audited blind: it caught up"


def test_entering_and_leaving_the_blind_state_are_each_audited_once() -> None:
    bank = MemoryBank()
    pledged(bank)
    bank.registry.deliver_element("no id")
    for _ in range(5):
        bank.outbox_sync().sync()
        bank.world.clock.advance(LIMIT)
    state = bank.world.store.state.outbox_state
    assert state.blind_since is not None and state.last_reason == "UNACKNOWLEDGEABLE"
    assert audited(bank, AuditAction.REGISTRY_OUTBOX_BLIND) == 1
    bank.registry.drop_unacknowledgeable()
    assert bank.outbox_sync().sync().complete
    assert audited(bank, AuditAction.REGISTRY_OUTBOX_RECOVERED) == 1
    assert bank.world.store.state.outbox_state.blind_since is None
