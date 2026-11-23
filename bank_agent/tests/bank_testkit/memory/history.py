"""`MemoryHistory`: the `HistoryReader` of the memory unit of work.

The stored tapes, the committed drafts chained the way the SQL writer chains them, and the rows
deep verification digests, replays and re-derives, with the columns and order of
`infrastructure/db/repositories/stage_rows.py`, so a digest made in memory and one made by
PostgreSQL agree for the same rows (the port tests compare the two).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING

from onepledge_bank_agent.domain.enums import RequestOrigin
from onepledge_bank_agent.domain.raw_evidence import StoredRow, StoredTape
from onepledge_bank_agent.domain.replay import State
from onepledge_bank_agent.domain.stage_digests import DigestRow, StagePart, body_sha256, row
from onepledge_bank_agent.domain.tape_file import raw_payload

if TYPE_CHECKING:
    from bank_testkit.memory.stores import MemoryState
    from onepledge_bank_agent.domain.audit import AuditEntry


class MemoryHistory:
    """Reads only; everything is read from the unit's copy of the state."""

    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def audit_entries(self) -> Iterator[AuditEntry]:
        return iter(sorted(self._state.audit_log, key=lambda entry: entry.seq))

    def audit_size(self) -> tuple[int, int]:
        return len(self._state.audit_log), max((entry.seq for entry in self._state.audit_log), default=0)

    def submission_ids(self) -> list[uuid.UUID]:
        ordered = sorted(self._state.tapes.values(), key=lambda t: (t.facts.uploaded_at, t.submission_id))
        return [t.submission_id for t in ordered]

    def loan_ids(self) -> set[uuid.UUID]:
        return set(self._state.loans)

    def tape(self, submission_id: uuid.UUID) -> StoredTape | None:
        tape = self._state.tapes.get(submission_id)
        layout = self._state.layouts.get(submission_id)
        if tape is None or layout is None:
            return None
        rows = tuple(
            StoredRow(r.row_number, r.text, r.cells, raw_payload(layout.header_cells, r.cells), r.row_sha256)
            for r in sorted(self._state.raw_rows.get(submission_id, []), key=lambda r: r.row_number)
        )
        facts = tape.facts
        return StoredTape(
            facts.submission_id, facts.source, facts.kind, facts.facility_id, facts.as_of_date,
            facts.file_sha256, facts.rows_sha256, facts.row_count, layout, rows,
        )  # fmt: skip

    def stage_rows(self, submission_id: uuid.UUID, part: StagePart) -> list[DigestRow]:
        items = sorted(self._state.items.get(submission_id, []), key=lambda i: i.row_number)
        if part is StagePart.ITEMS:
            return [row(i.row_number, i.disposition, i.loan_id) for i in items]
        if part is StagePart.ISSUES:
            return sorted(row(i.row_number, s.field, s.problem, s.action) for i in items for s in i.issues)
        if part is StagePart.CHECKS:
            return sorted(
                row(loan_id, c.check_type, c.outcome, c.reason, c.evidence)
                for (held, loan_id), results in self._state.checks.items()
                if held == submission_id
                for c in results
            )
        requests = self._tape_requests(submission_id)
        if part is StagePart.REQUESTS:
            return self.request_rows(requests)
        pairs = {
            (item.loan_id, str(self._state.requests[request_id].key_version))
            for request_id in requests
            for item in self._state.request_items.get(request_id, [])
        }
        return sorted(
            row(loan_id, t.kind, str(t.key_version), t.value.value)
            for loan_id, held in self._state.tokens.items()
            for t in held
            if (loan_id, str(t.key_version)) in pairs
        )

    def _tape_requests(self, submission_id: uuid.UUID) -> list[uuid.UUID]:
        held = [
            r
            for r in self._state.requests.values()
            if r.submission_id == submission_id and r.origin is RequestOrigin.TAPE
        ]
        return [r.request_id for r in sorted(held, key=lambda r: r.batch_index)]

    def request_rows(self, request_ids: Sequence[uuid.UUID]) -> list[DigestRow]:
        found = []
        for request_id in request_ids:
            request = self._state.requests.get(request_id)
            if request is None:
                found.append(row(str(request_id), None))
                continue
            items = sorted(self._state.request_items.get(request_id, []), key=lambda i: i.position)
            empty: tuple[object, ...] = (None, None, None, None, (), None, None, None)
            held = [
                row(
                    i.item_ref,
                    i.position,
                    i.loan_id,
                    *self._state.results.get((request_id, i.item_ref), empty),
                )
                for i in items
            ]
            answer = self._state.answers.get(request_id)
            found.append(
                row(
                    request.idempotency_key.value, str(request.key_version), request.item_count,
                    request.response_status, self._state.responded.get(request_id),
                    request.registry_submission_id, request.as_of_seq, request.certificate_id,
                    body_sha256(answer.body if answer is not None else None), held,
                )
            )  # fmt: skip
        return found

    def reference_rows(self) -> dict[str, list[DigestRow]]:
        lenders = sorted(self._state.lenders.values(), key=lambda lender: lender.lender_id)
        facilities = sorted(self._state.facilities.values(), key=lambda facility: facility.facility_id)
        return {
            "lenders": [row(x.lender_id, x.legal_name, x.lei.value) for x in lenders],
            "facilities": [
                row(
                    f.facility_id,
                    f.lender_id,
                    f.code,
                    f.name,
                    f.credit_limit_cents,
                    f.advance_rate_bps,
                    f.status,
                    f.opened_on,
                )
                for f in facilities
            ],
        }

    def states(self, table: str) -> dict[uuid.UUID, State]:
        if table == "loans":
            return {key: (loan.state.value,) for key, loan in self._state.loans.items()}
        if table == "tape_submissions":
            return {key: (tape.status.value,) for key, tape in self._state.tapes.items()}
        if table == "alerts":
            return {
                key: (alert.status.value, alert.record.case_status.value)
                for key, alert in self._state.alerts.items()
            }
        return {key: (role, str(active).lower()) for key, (role, active) in self._state.users.items()}

    def loan_values(self) -> dict[uuid.UUID, DigestRow]:
        found = {}
        for key, loan in self._state.loans.items():
            terms, record = loan.terms, loan.record
            amount, balance = terms.original_amount.cents, record.current_balance.cents
            found[key] = row(
                terms.vin.value, terms.borrower_ref.value, terms.origination_date, amount, balance,
                record.lender_loan_number.value,
            )  # fmt: skip
        return found
