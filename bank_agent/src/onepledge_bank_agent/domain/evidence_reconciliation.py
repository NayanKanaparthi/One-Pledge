"""Evidence that has disappeared is a finding (SPEC_DECISIONS 89, b and c).

Every audit entry that records the creation of a proof names the row's primary key as its
object. Deep verification compares, per table, the ids the audit log names with the
primary keys the table holds: an id with no row is ROW_MISSING, a row with no entry is
ROW_UNAUDITED. Stored checkpoint sequence numbers must be consecutive (SEQUENCE_GAP). A
tape must hold its `row_count` raw rows, and every loan its creating LOAN_STATE_CHANGED
entry. The end of the audit log a previous deep verification saw must still be there.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from types import MappingProxyType

from onepledge_bank_agent.domain.audit import AuditEntry
from onepledge_bank_agent.domain.enums import AuditAction
from onepledge_bank_agent.domain.integrity import EvidenceProblem, EvidenceProblemKind

LOANS = "loans"
PROOF_TABLES: Mapping[str, str] = MappingProxyType(
    {
        AuditAction.CERTIFICATE_VERIFIED.value: "certificates",
        AuditAction.CERTIFICATE_REJECTED.value: "certificates",
        AuditAction.CHECKPOINT_VERIFIED.value: "checkpoints_seen",
        AuditAction.CHECKPOINT_REJECTED.value: "checkpoints_seen",
        AuditAction.REGISTRY_MESSAGE_QUARANTINED.value: "registry_messages_quarantined",
        AuditAction.INTEGRITY_VERIFICATION_RUN.value: "integrity_runs",
        AuditAction.TAPE_UPLOADED.value: "tape_submissions",
        AuditAction.RELEASE_REQUESTED.value: "tape_submissions",
    }
)
"""The action that records the creation of a row, and the row's table."""


def audited_ids(entries: Iterable[AuditEntry]) -> dict[str, set[uuid.UUID]]:
    """Per table, the primary keys the audit log says were created; for `loans`, the loans whose
    creating LOAN_STATE_CHANGED entry (from null) exists."""
    found: dict[str, set[uuid.UUID]] = {table: set() for table in (*PROOF_TABLES.values(), LOANS)}
    for entry in entries:
        if entry.object_id is None:
            continue
        table = PROOF_TABLES.get(entry.action)
        if table is not None:
            found[table].add(entry.object_id)
        elif entry.action == AuditAction.LOAN_STATE_CHANGED.value and entry.detail.get("from") is None:
            found[LOANS].add(entry.object_id)
    return found


def reconcile(
    audited: Mapping[str, set[uuid.UUID]], rows: Mapping[str, set[uuid.UUID]]
) -> list[EvidenceProblem]:
    """ROW_MISSING for every audited id without its row, ROW_UNAUDITED for every row without its entry.
    `loans` are anchors, not proofs: only a loan without its creating entry is reported."""
    problems: list[EvidenceProblem] = []
    for table, held in rows.items():
        named = audited.get(table, set())
        if table != LOANS:
            problems += [
                EvidenceProblem(table, str(i), EvidenceProblemKind.ROW_MISSING) for i in named - held
            ]
        problems += [EvidenceProblem(table, str(i), EvidenceProblemKind.ROW_UNAUDITED) for i in held - named]
    return sorted(problems)


def sequence_gaps(table: str, sequence_numbers: Iterable[int]) -> list[EvidenceProblem]:
    """Every seq missing between the lowest and the highest stored one."""
    held = set(sequence_numbers)
    if not held:
        return []
    return [
        EvidenceProblem(table, str(seq), EvidenceProblemKind.SEQUENCE_GAP)
        for seq in range(min(held), max(held) + 1)
        if seq not in held
    ]


def missing_tail_seq(last_seq: int, last_entry_hash: str, entries: Mapping[int, str]) -> int | None:
    """The seq a previous deep verification saw last, when the log no longer holds it with its hash."""
    if entries.get(last_seq) == last_entry_hash:
        return None
    return last_seq
