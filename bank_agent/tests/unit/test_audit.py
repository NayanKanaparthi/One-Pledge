"""The audit entry hash rule and chain of SPEC 17.1, checked against an independent recomputation."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise

import pytest

from onepledge_bank_agent.domain.audit import (
    GENESIS_PREV_HASH,
    Actor,
    AuditDraft,
    chain_entry,
    first_broken_seq,
    render_audit_time,
)
from onepledge_bank_agent.domain.enums import ActorType, AuditAction, AuditObjectType, AuditOutcome, Role
from onepledge_bank_agent.domain.errors import InvalidValueError

pytestmark = pytest.mark.unit

AT = datetime(2026, 11, 20, 9, 5, 7, 123, tzinfo=UTC)
USER = uuid.UUID("11111111-1111-4111-8111-111111111111")


def draft(action: AuditAction = AuditAction.TAPE_UPLOADED) -> AuditDraft:
    return AuditDraft(
        action, Actor.user(USER, Role.ANALYST), AuditObjectType.TAPE_SUBMISSION, uuid.uuid4(),
        AuditOutcome.SUCCESS, {"kind": "PLEDGE", "row_count": 20}, "req-1",
    )  # fmt: skip


def test_times_render_with_six_fraction_digits_in_utc() -> None:
    assert render_audit_time(AT) == "2026-11-20T09:05:07.000123Z"
    assert render_audit_time(AT.astimezone(timezone(timedelta(hours=5)))) == "2026-11-20T09:05:07.000123Z"
    with pytest.raises(InvalidValueError):
        render_audit_time(datetime(2026, 1, 1, tzinfo=None))


def test_the_first_entry_links_to_zeros_and_hashes_the_twelve_fields() -> None:
    audit_id = uuid.uuid4()
    entry = chain_entry(draft(), audit_id, AT, None)
    assert (entry.seq, entry.prev_hash) == (1, "0" * 64) and GENESIS_PREV_HASH == "0" * 64
    fields = {
        "action": "TAPE_UPLOADED", "actor_type": "USER", "actor_user_id": str(USER),
        "at": "2026-11-20T09:05:07.000123Z", "audit_id": str(audit_id),
        "detail": {"kind": "PLEDGE", "row_count": 20},
        "object_id": str(entry.object_id), "object_type": "TAPE_SUBMISSION", "outcome": "SUCCESS",
        "prev_hash": "0" * 64, "request_id": "req-1", "seq": 1,
    }  # fmt: skip
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert entry.entry_hash == hashlib.sha256(canonical).hexdigest()


def test_entries_chain_and_any_edit_or_gap_is_located() -> None:
    entries = []
    previous = None
    for index in range(5):
        entry = chain_entry(draft(), uuid.uuid4(), AT + timedelta(seconds=index), previous)
        entries.append(entry)
        previous = (entry.seq, entry.entry_hash)
    assert [e.seq for e in entries] == [1, 2, 3, 4, 5]
    assert all(b.prev_hash == a.entry_hash for a, b in pairwise(entries))
    assert first_broken_seq(entries) is None
    edited = [*entries[:2], replace(entries[2], detail={"kind": "RELEASE"}), *entries[3:]]
    assert first_broken_seq(edited) == 3
    assert first_broken_seq([*entries[:3], *entries[4:]]) == 4
    relinked = [*entries[:3], replace(entries[3], prev_hash="f" * 64), entries[4]]
    assert first_broken_seq(relinked) == 4


def test_a_system_actor_has_no_user_and_a_user_actor_needs_both() -> None:
    system = chain_entry(
        AuditDraft(AuditAction.TAPE_STAGE_ADVANCED, Actor.system(), AuditObjectType.TAPE_SUBMISSION, None),
        uuid.uuid4(),
        AT,
        None,
    )
    assert system.actor_type is ActorType.SYSTEM and system.actor_user_id is None and system.object_id is None
    with pytest.raises(InvalidValueError):
        Actor(ActorType.USER, USER, None)
    with pytest.raises(InvalidValueError):
        Actor(ActorType.SYSTEM, USER, Role.ADMIN)
