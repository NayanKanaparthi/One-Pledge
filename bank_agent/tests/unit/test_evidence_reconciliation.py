"""The rules of SPEC_DECISIONS 89 (b) and (c), on plain values: ids against rows, gaps, the audit tail."""

from __future__ import annotations

import uuid

import pytest

from bank_testkit.records import T0
from onepledge_bank_agent.domain.audit import AuditEntry
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.enums import ActorType, AuditAction
from onepledge_bank_agent.domain.evidence_reconciliation import (
    PROOF_TABLES,
    audited_ids,
    missing_tail_seq,
    reconcile,
    sequence_gaps,
)
from onepledge_bank_agent.domain.integrity import EvidenceProblem, EvidenceProblemKind

pytestmark = pytest.mark.unit

MISSING, UNAUDITED, GAP = (
    EvidenceProblemKind.ROW_MISSING,
    EvidenceProblemKind.ROW_UNAUDITED,
    EvidenceProblemKind.SEQUENCE_GAP,
)


def entry(
    seq: int, action: AuditAction, object_id: uuid.UUID | None, detail: dict[str, JsonValue]
) -> AuditEntry:
    return AuditEntry(
        uuid.uuid4(), seq, T0, ActorType.SYSTEM, None, action.value, "OBJECT", object_id, "SUCCESS", None,
        detail, "0" * 64, "1" * 64,
    )  # fmt: skip


def test_each_creating_action_names_a_row_of_its_table_and_only_a_loans_first_move_counts() -> None:
    proof, loan, later = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    entries = [
        entry(1, AuditAction.CERTIFICATE_VERIFIED, proof, {}),
        entry(2, AuditAction.LOAN_STATE_CHANGED, loan, {"from": None, "to": "PENDING"}),
        entry(3, AuditAction.LOAN_STATE_CHANGED, later, {"from": "PENDING", "to": "LOCKED"}),
        entry(4, AuditAction.TAPE_UPLOADED, None, {}),
    ]
    found = audited_ids(entries)
    assert found["certificates"] == {proof}
    assert found["loans"] == {loan}
    assert set(found) == {*PROOF_TABLES.values(), "loans"}
    assert all(not ids for table, ids in found.items() if table not in ("certificates", "loans"))


def test_an_audited_id_without_its_row_is_missing_and_a_row_without_its_entry_is_unaudited() -> None:
    kept, deleted, unaudited = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    loan, bare_loan = uuid.uuid4(), uuid.uuid4()
    problems = reconcile(
        {"certificates": {kept, deleted}, "loans": {loan, uuid.uuid4()}},
        {"certificates": {kept, unaudited}, "loans": {loan, bare_loan}},
    )
    assert problems == sorted([
        EvidenceProblem("certificates", str(deleted), MISSING),
        EvidenceProblem("certificates", str(unaudited), UNAUDITED),
        EvidenceProblem("loans", str(bare_loan), UNAUDITED),
    ])  # fmt: skip
    assert reconcile({"certificates": {kept}}, {"certificates": {kept}}) == []


def test_every_hole_between_the_lowest_and_the_highest_seq_is_a_gap() -> None:
    assert sequence_gaps("checkpoints_seen", [1, 2, 5, 7]) == [
        EvidenceProblem("checkpoints_seen", str(seq), GAP) for seq in (3, 4, 6)
    ]
    assert sequence_gaps("checkpoints_seen", [3, 4]) == []
    assert sequence_gaps("checkpoints_seen", []) == []


def test_the_tail_must_still_hold_its_seq_with_its_hash() -> None:
    assert missing_tail_seq(5, "h5", {4: "h4", 5: "h5", 6: "h6"}) is None
    assert missing_tail_seq(5, "h5", {4: "h4"}) == 5, "deleted"
    assert missing_tail_seq(5, "h5", {5: "other"}) == 5, "rewritten"
