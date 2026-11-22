"""Poison registry messages: contain, record, acknowledge, continue (SPEC_DECISIONS 85; review 2, F1 and F2).

Each test asserts the whole class: the message is quarantined with the stated reason,
the audit entry names the quarantine row, the message is acknowledged, the next message
of the same poll is applied, a tape uploaded afterwards advances, and the integrity
report shows `registry_messages` BROKEN.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

import pytest

from bank_testkit.memory.bank import MemoryBank, vins
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.dependency_errors import RegistryUnavailableError
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
from onepledge_bank_agent.domain.registry_records import OutboxMessage

pytestmark = pytest.mark.unit

AT = "2026-11-22T00:00:00Z"
SIGNATURE = "A" * 86 + "=="


def asset_of(bank: MemoryBank, vin: str) -> str:
    loan = bank.loans()[vin]
    return next(
        t.value.value for t in bank.world.store.state.tokens[loan.loan_id] if t.kind is TokenKind.ASSET
    )


def checkpoint(**body: JsonValue) -> dict[str, JsonValue]:
    """A CHECKPOINT message of review F1: valid against the schema, numbers as given."""
    fields: dict[str, JsonValue] = {
        "schema_version": 1, "checkpoint_seq": 1, "last_event_seq": 9000, "head_hash": "a" * 64,
        "signed_at": AT, **body,
    }  # fmt: skip
    payload: dict[str, JsonValue] = {
        "body": fields,
        "signature": SIGNATURE,
        "signing_key_id": "registry-local-1",
    }
    return {"type": "CHECKPOINT", "created_at": AT, "payload": payload}


def alert(asset_token: str, case_id: uuid.UUID, **changes: JsonValue) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "case_id": str(case_id), "role": "HOLDER", "asset_token": asset_token, "match_kind": "EXACT",
        "match_basis": "ASSET", "kinds_matched": ["ASSET"], "opened_seq": 4, **changes,
    }  # fmt: skip
    return {
        "type": "ALERT",
        "created_at": AT,
        "payload": {k: v for k, v in payload.items() if v != "<absent>"},
    }


def case_update(case_id: uuid.UUID, token: str, status: str, resolved_seq: int) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "case_id": str(case_id), "asset_token": token, "status": status, "resolved_seq": resolved_seq,
    }  # fmt: skip
    return {"type": "CASE_UPDATE", "created_at": AT, "payload": payload}


def contained(bank: MemoryBank, message: OutboxMessage, reason: QuarantineReason) -> None:
    """Quarantined with the reason, audited against the row, acknowledged, and the report BROKEN."""
    state = bank.world.store.state
    (held,) = [row for row in state.quarantined if row.message_id == message.message_id]
    assert held.quarantined.reason is reason
    assert held.quarantined.payload_sha256 == message.evidence.payload_sha256
    audited = [
        d
        for d in bank.world.audit(AuditAction.REGISTRY_MESSAGE_QUARANTINED)
        if d.object_id == held.quarantine_id
    ]
    assert [entry.detail["reason"] for entry in audited] == [reason.value]
    assert message.message_id not in {pending.message_id for pending in bank.registry.pending()}, (
        "acknowledged"
    )
    assert bank.report().registry_messages.status is SectionStatus.BROKEN


def pledged(bank: MemoryBank) -> tuple[str, uuid.UUID]:
    """A LOCKED loan's asset code, and a case the registry opened on it."""
    (car,) = vins(1, start=60)
    bank.pledge([car])
    return asset_of(bank, car), bank.registry.alert(asset_of(bank, car))


def advances(bank: MemoryBank, halted: bool = False) -> None:
    """A tape uploaded now runs to COMPLETED, or, when a checkpoint is in quarantine, to TOKENIZED."""
    tape = bank.upload(vins(1, start=80))
    last = bank.run(tape)[-1]
    if halted:
        assert bank.tape(tape).progress.resume_status is TapeStatus.TOKENIZED and bank.halt_rule().active()
        assert last.failure is not None and last.failure["failure_code"] == "REGISTRY_INTEGRITY_BROKEN"
    else:
        assert last.status is TapeStatus.COMPLETED


@pytest.mark.parametrize(
    ("body", "name"),
    [
        ({"checkpoint_seq": 2**63}, "a sequence number of 2^63"),
        ({"last_event_seq": 10**30}, "a 31 digit last_event_seq"),
    ],
)
def test_a_checkpoint_with_a_number_too_large_to_store_is_quarantined_and_halts(
    body: Mapping[str, JsonValue], name: str
) -> None:
    bank = MemoryBank()
    _, case_id = pledged(bank)
    poison = bank.registry.deliver_wire(checkpoint(**body))
    result = bank.outbox_sync().sync()
    assert result.messages_processed == 2, name
    contained(bank, poison, QuarantineReason.VALUE_OUT_OF_RANGE)
    assert [a.record.registry_case_id for a in bank.alerts()] == [case_id], "the next message was applied"
    assert bank.world.store.state.checkpoints == []
    advances(bank, halted=True)


def test_a_case_update_that_contradicts_a_closed_alert_is_quarantined_and_the_alert_is_kept() -> None:
    """Review F2: a second, different CASE_UPDATE for a closed case."""
    bank = MemoryBank()
    token, case_id = pledged(bank)
    bank.registry.case_update(case_id, CaseStatus.CLEARED_DATA_ERROR)
    bank.outbox_sync().sync()
    poison = bank.registry.deliver_wire(case_update(case_id, token, "CONFIRMED", 600))
    repeat = bank.registry.deliver_wire(case_update(case_id, token, "CLEARED_DATA_ERROR", 6))
    other_case = bank.registry.alert(token)
    assert bank.outbox_sync().sync().messages_processed == 3
    contained(bank, poison, QuarantineReason.CONTRADICTS_STORED_STATE)
    assert repeat.message_id not in {row.message_id for row in bank.world.store.state.quarantined}, "a repeat"
    closed = next(a for a in bank.alerts() if a.record.registry_case_id == case_id)
    assert (closed.status, closed.record.case_status) == (AlertStatus.CLEARED, CaseStatus.CLEARED_DATA_ERROR)
    assert other_case in {a.record.registry_case_id for a in bank.alerts()}
    advances(bank)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"kinds_matched": ["ASSET", "VIN"]}, QuarantineReason.CONTRADICTS_STORED_STATE),
        ({"kinds_matched": ["ASSET", "ASSET", "VIN"]}, QuarantineReason.SCHEMA_MISMATCH),
        ({"opened_seq": "<absent>"}, QuarantineReason.SCHEMA_MISMATCH),
    ],
    ids=["repeated with other kinds", "duplicate kinds", "no opened_seq"],
)
def test_an_alert_that_contradicts_or_breaks_the_contract_is_quarantined(
    changes: Mapping[str, JsonValue], reason: QuarantineReason
) -> None:
    bank = MemoryBank()
    token, case_id = pledged(bank)
    bank.outbox_sync().sync()
    poison = bank.registry.deliver_wire(alert(token, case_id, **changes))
    following = bank.registry.alert(token)
    bank.outbox_sync().sync()
    contained(bank, poison, reason)
    (kept,) = [a for a in bank.alerts() if a.record.registry_case_id == case_id]
    assert kept.record.match.kinds_matched == (TokenKind.ASSET,), "the stored alert is not touched"
    assert following in {a.record.registry_case_id for a in bank.alerts()}
    advances(bank)


def test_a_handler_raising_an_unexpected_exception_is_quarantined_and_logged_as_an_error() -> None:
    bank = MemoryBank()
    token, case_id = pledged(bank)
    bank.world.faults.arm(FaultPoint.OUTBOX_HANDLER_BEFORE_COMMIT, error=LookupError("not anticipated"))
    poison = next(m for m in bank.registry.pending() if m.message_type == "ALERT")
    following = bank.registry.alert(token)
    bank.outbox_sync().sync()
    contained(bank, poison, QuarantineReason.UNEXPECTED_ERROR)
    assert case_id not in {a.record.registry_case_id for a in bank.alerts()}, "rolled back"
    assert following in {a.record.registry_case_id for a in bank.alerts()}
    errors = [fields for level, event, fields in bank.world.log.events if level == "error"]
    assert [(f["exception_type"], f["kind"]) for f in errors] == [("LookupError", "ALERT")]
    advances(bank)


def test_a_message_deferred_five_times_becomes_poison() -> None:
    bank = MemoryBank()
    pledged(bank)
    bank.outbox_sync().sync()
    bank.registry.checkpoint()
    poison = bank.registry.pending()[-1]
    following = bank.registry.deliver("FUTURE_TYPE", None)
    outage = RegistryUnavailableError(None, FailureReason.CONNECTION_FAILED)
    bank.registry.fail("event_headers", outage, times=5)
    for attempt in range(1, 5):
        with pytest.raises(RegistryUnavailableError):
            bank.outbox_sync().sync()
        assert bank.world.store.state.outbox_attempts[poison.message_id][0] == attempt
        assert [m.message_id for m in bank.registry.pending()][:1] == [poison.message_id], "not acknowledged"
        assert bank.world.store.state.quarantined == []
    assert bank.outbox_sync().sync().messages_processed == 2
    contained(bank, poison, QuarantineReason.REPEATED_FAILURE)
    assert following.message_id not in {m.message_id for m in bank.registry.pending()}
    advances(bank, halted=True)


def test_a_sync_that_raises_never_starves_a_waiting_tape() -> None:
    bank = MemoryBank()
    tape = bank.upload(vins(1, start=90))
    bank.registry.deliver("FUTURE_TYPE", None)
    bank.world.faults.arm(FaultPoint.BETWEEN_OUTBOX_COMMIT_AND_ACK, error=LookupError("sync broke"))
    report = bank.worker().run_once()
    assert bank.tape(tape).status is TapeStatus.COMPLETED, "every stage ran after the failed sync"
    assert report.task_failures == ["INTERNAL_ERROR"]


@pytest.mark.parametrize(
    "spelling",
    [str.upper, lambda text: "{" + text + "}", lambda text: text.replace("-", "")],
    ids=["upper case", "braces", "no hyphens"],
)
def test_a_message_whose_ids_are_not_canonical_is_quarantined_and_acknowledged_by_its_id(
    spelling: object,
) -> None:
    """SPEC_DECISIONS 75 with 85 rule 1 (K18): a message id or case id in another spelling is poison,
    SCHEMA_MISMATCH; it is acknowledged by the id it carries, and the sync goes on."""
    assert callable(spelling)
    bank = MemoryBank()
    token, case_id = pledged(bank)
    bank.outbox_sync().sync()
    message_id = uuid.uuid4()
    bad_id = bank.registry.deliver_wire(
        {**alert(token, uuid.uuid4()), "message_id": spelling(str(message_id))}
    )
    wire = alert(token, uuid.uuid4())
    payload = wire["payload"]
    assert isinstance(payload, dict)
    bad_case = bank.registry.deliver_wire(
        {**wire, "payload": {**payload, "case_id": spelling(str(uuid.uuid4()))}}
    )
    following = bank.registry.alert(token)
    assert bank.outbox_sync().sync().messages_processed == 3
    assert bad_id.message_id == message_id
    contained(bank, bad_id, QuarantineReason.SCHEMA_MISMATCH)
    contained(bank, bad_case, QuarantineReason.SCHEMA_MISMATCH)
    assert {a.record.registry_case_id for a in bank.alerts()} == {case_id, following}
    advances(bank)
