"""The evidence checks of deep verification (SPEC_DECISIONS 89 b and c, 107), read in one unit of work.

`evidence_problems` reconciles every proof table and the loan, tape and alert anchors with the
audit log (`domain/evidence_reconciliation.py`), finds holes in the stored checkpoint
sequence and tapes whose raw rows are fewer than their `row_count`, then recomputes every stage
digest, derives cleaning and loan terms again and replays loans, tapes, alerts and users
(`deep_evidence.py`). `audit_tail` is the end of the log now, which the next deep verification
requires to still be there.
"""

from __future__ import annotations

from onepledge_bank_agent.application.deep_evidence import (
    derivation_problems,
    digest_problems,
    replay_problems,
)
from onepledge_bank_agent.application.ports import UnitOfWork
from onepledge_bank_agent.domain.audit import AuditEntry
from onepledge_bank_agent.domain.evidence_reconciliation import (
    audited_ids,
    missing_tail_seq,
    reconcile,
    sequence_gaps,
)
from onepledge_bank_agent.domain.integrity import EvidenceProblem, EvidenceProblemKind
from onepledge_bank_agent.domain.integrity_document import AuditTail

RAW_ROWS = "loans_raw"
CHECKPOINTS = "checkpoints_seen"


def evidence_problems(unit: UnitOfWork, entries: list[AuditEntry]) -> list[EvidenceProblem]:
    """Every ROW_MISSING, ROW_UNAUDITED, SEQUENCE_GAP, DIGEST_MISMATCH, DERIVATION_MISMATCH and
    STATE_MISMATCH the stored evidence shows now."""
    checkpoints = unit.evidence.checkpoints.all()
    submissions = unit.history.submission_ids()
    rows = {
        "certificates": {row.certificate_row_id for row in unit.evidence.certificates.all()},
        CHECKPOINTS: {row.checkpoint_id for row in checkpoints},
        "registry_messages_quarantined": {row.quarantine_id for row in unit.evidence.quarantine.all()},
        "integrity_runs": unit.evidence.runs.ids(),
        "tape_submissions": set(submissions),
        "loans": unit.history.loan_ids(),
        "alerts": set(unit.history.states("alerts")),
    }
    problems = reconcile(audited_ids(entries), rows)
    problems += sequence_gaps(CHECKPOINTS, [row.checkpoint_seq for row in checkpoints])
    for submission_id in submissions:
        tape = unit.history.tape(submission_id)
        if tape is not None and len(tape.rows) < tape.row_count:
            problems.append(EvidenceProblem(RAW_ROWS, str(submission_id), EvidenceProblemKind.ROW_MISSING))
    problems += digest_problems(unit.history, entries)
    problems += derivation_problems(unit.history)
    problems += replay_problems(unit.history, entries)
    return problems


def audit_tail(entries: list[AuditEntry]) -> AuditTail | None:
    """The last entry of the log now, or None for an empty log."""
    if not entries:
        return None
    last = max(entries, key=lambda entry: entry.seq)
    return AuditTail(last.seq, last.entry_hash)


def tail_missing(previous: AuditTail | None, entries: list[AuditEntry]) -> int | None:
    """AUDIT_TAIL_MISSING: the seq a previous deep verification saw last, when it is gone or changed."""
    if previous is None:
        return None
    return missing_tail_seq(
        previous.last_seq, previous.last_entry_hash, {e.seq: e.entry_hash for e in entries}
    )
