"""Deep verification of every decision the bank acts on (SPEC_DECISIONS 107).

- Stage digests (a): what each stage produced is digested into its `TAPE_STAGE_ADVANCED` entry
  (`stage_digests`), a resubmission's request into `LOAN_RESUBMITTED` (`request_digest`), the
  reference data into `REFERENCE_DATA_SEEDED` (`reference_digests`). `digest_problems` recomputes
  each from the tables: `DIGEST_MISMATCH` on the table, with the submission (or request) and stage.
- Re-derivation (b): cleaning is a pure function of the raw row, so every row of an uploaded file
  is cleaned again; each issue it gives must be stored for that row, and every loan's terms and
  balance must equal what its most recent accepted row gives (its lender loan number, what one of
  its accepted rows gave). `DERIVATION_MISMATCH` names the row or the loan.
- Replay (c): loans, tapes, alerts and users against their audit entries (`domain/replay.py`).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from onepledge_bank_agent.application.evidence_ports import HistoryReader
from onepledge_bank_agent.domain.audit import AuditEntry
from onepledge_bank_agent.domain.cleaning import CleanedRow, HeaderMap, clean_row
from onepledge_bank_agent.domain.enums import AuditAction, RowDisposition, TapeSource, TapeStatus
from onepledge_bank_agent.domain.integrity import EvidenceProblem, EvidenceProblemKind
from onepledge_bank_agent.domain.replay import (
    ALERT_ACTIONS,
    LOAN_ACTIONS,
    TAPE_ACTIONS,
    USER_ACTIONS,
    alert_step,
    loan_step,
    replay,
    tape_step,
    user_step,
)
from onepledge_bank_agent.domain.stage_digests import (
    PART_TABLES,
    STAGE_PARTS,
    DigestRow,
    StagePart,
    row,
    rows_sha256,
)

ACCEPTED = frozenset(
    d.value
    for d in (RowDisposition.ACCEPTED_NEW, RowDisposition.ACCEPTED_REPRESENTED, RowDisposition.ALREADY_LOCKED)
)
REFERENCE_PARTS = ("lenders", "facilities")


def stage_digests(history: HistoryReader, submission_id: uuid.UUID, reached: TapeStatus) -> dict[str, str]:
    """The digests the entry for the move to `reached` carries (none for the other stages)."""
    parts = STAGE_PARTS.get(reached, ())
    return {part.value: rows_sha256(history.stage_rows(submission_id, part)) for part in parts}


def request_digest(history: HistoryReader, request_id: uuid.UUID) -> dict[str, str]:
    """`requests_sha256` of one request, for `LOAN_RESUBMITTED`."""
    return {StagePart.REQUESTS.value: rows_sha256(history.request_rows([request_id]))}


def reference_digests(history: HistoryReader) -> dict[str, str]:
    """`content_sha256` of lenders and facilities, and the digest of each, for `REFERENCE_DATA_SEEDED`."""
    rows = history.reference_rows()
    parts = {f"{name}_sha256": rows_sha256(rows[name]) for name in REFERENCE_PARTS}
    return {"content_sha256": rows_sha256([row(*parts.values())]), **parts}


def _latest(entries: Iterable[AuditEntry], action: AuditAction) -> list[AuditEntry]:
    return sorted((e for e in entries if e.action == action.value), key=lambda e: e.seq)


MISMATCH = EvidenceProblemKind.DIGEST_MISMATCH
DERIVED = EvidenceProblemKind.DERIVATION_MISMATCH


def digest_problems(history: HistoryReader, entries: Sequence[AuditEntry]) -> list[EvidenceProblem]:
    """DIGEST_MISMATCH for every stored digest the tables no longer give."""
    return [
        *_stage_problems(history, entries),
        *_resubmission_problems(history, entries),
        *_reference_problems(history, entries),
    ]


def _stage_problems(history: HistoryReader, entries: Sequence[AuditEntry]) -> list[EvidenceProblem]:
    """The latest entry for the move of each submission to each status, against the tables."""
    latest: dict[tuple[uuid.UUID, str], AuditEntry] = {}
    for entry in _latest(entries, AuditAction.TAPE_STAGE_ADVANCED):
        if entry.object_id is not None:
            latest[(entry.object_id, str(entry.detail.get("to")))] = entry
    problems = []
    for (submission_id, reached), entry in sorted(latest.items(), key=lambda item: item[1].seq):
        status = TapeStatus(reached) if reached in TapeStatus else TapeStatus.RECEIVED
        for part in STAGE_PARTS.get(status, ()):
            stored = entry.detail.get(part.value)
            if stored is not None and stored != rows_sha256(history.stage_rows(submission_id, part)):
                problems.append(EvidenceProblem(PART_TABLES[part], str(submission_id), MISMATCH, status))
    return problems


def _resubmission_problems(history: HistoryReader, entries: Sequence[AuditEntry]) -> list[EvidenceProblem]:
    problems = []
    for entry in _latest(entries, AuditAction.LOAN_RESUBMITTED):
        request_id, stored = (
            entry.detail.get("registry_request_id"),
            entry.detail.get(StagePart.REQUESTS.value),
        )
        if not isinstance(request_id, str) or stored is None:
            continue
        if stored != request_digest(history, uuid.UUID(request_id))[StagePart.REQUESTS.value]:
            problems.append(EvidenceProblem("registry_requests", request_id, MISMATCH))
    return problems


def _reference_problems(history: HistoryReader, entries: Sequence[AuditEntry]) -> list[EvidenceProblem]:
    seeded = [e for e in _latest(entries, AuditAction.REFERENCE_DATA_SEEDED) if "content_sha256" in e.detail]
    if not seeded:
        return []
    now = reference_digests(history)
    return [
        EvidenceProblem(name, None, MISMATCH)
        for name in REFERENCE_PARTS
        if seeded[-1].detail.get(f"{name}_sha256") != now[f"{name}_sha256"]
    ]


def _rows_by_number(rows: Sequence[DigestRow]) -> Mapping[object, list[DigestRow]]:
    found: dict[object, list[DigestRow]] = defaultdict(list)
    for item in rows:
        found[item[0]].append(item[1:])
    return found


def derivation_problems(history: HistoryReader) -> list[EvidenceProblem]:
    """DERIVATION_MISMATCH for a row whose issues cleaning no longer gives, and for a loan whose terms
    or balance its accepted rows no longer give."""
    problems: list[EvidenceProblem] = []
    latest: dict[uuid.UUID, DigestRow] = {}
    numbers: dict[uuid.UUID, set[object]] = defaultdict(set)
    for submission_id in history.submission_ids():
        tape = history.tape(submission_id)
        if tape is None or tape.source is not TapeSource.FILE_UPLOAD:
            continue
        header = HeaderMap.from_header(tape.layout.header_cells, tape.kind)
        stored_issues = _rows_by_number(history.stage_rows(submission_id, StagePart.ISSUES))
        items = {item[0]: item for item in history.stage_rows(submission_id, StagePart.ITEMS)}
        for raw in tape.rows:
            result = clean_row(raw.raw_cells, header, tape.kind, tape.as_of_date)
            derived = {row(i.field, i.problem, i.action) for i in result.issues}
            if not derived <= set(stored_issues.get(raw.row_number, [])):
                where = f"{submission_id}/{raw.row_number}"
                problems.append(EvidenceProblem("cleaning_issues", where, DERIVED))
            item = items.get(raw.row_number)
            values = result.loan if isinstance(result, CleanedRow) else None
            if item is None or item[1] not in ACCEPTED or not isinstance(item[2], str) or values is None:
                continue
            loan_id = uuid.UUID(item[2])
            terms = values.terms
            amount, balance = terms.original_amount.cents, values.current_balance.cents
            latest[loan_id] = row(
                terms.vin.value, terms.borrower_ref.value, terms.origination_date, amount, balance
            )
            numbers[loan_id].add(values.lender_loan_number.value)
    for loan_id, stored in sorted(history.loan_values().items(), key=lambda item: str(item[0])):
        if latest.get(loan_id) != stored[:5] or stored[5] not in numbers[loan_id]:
            problems.append(EvidenceProblem("loans", str(loan_id), DERIVED))
    return problems


def replay_problems(history: HistoryReader, entries: Sequence[AuditEntry]) -> list[EvidenceProblem]:
    """STATE_MISMATCH for every loan, tape, alert and user its entries do not lead to."""
    return [
        *replay("loans", LOAN_ACTIONS, loan_step, entries, history.states("loans")),
        *replay("tape_submissions", TAPE_ACTIONS, tape_step, entries, history.states("tape_submissions")),
        *replay("alerts", ALERT_ACTIONS, alert_step, entries, history.states("alerts")),
        *replay("users", USER_ACTIONS, user_step, entries, history.states("users")),
    ]
