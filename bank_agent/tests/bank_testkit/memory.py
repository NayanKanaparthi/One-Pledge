"""In-memory implementations of the persistence ports, for unit tests of the use cases (no I/O).

A unit of work copies the store when it opens and replaces it on commit, so leaving
without commit discards everything, as a database transaction would.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Self

from onepledge_bank_agent.application.ports import AcceptedItem, NewItem, StoredRawRow, Upserted
from onepledge_bank_agent.domain.audit import AuditDraft
from onepledge_bank_agent.domain.entities import Facility, FacilityWithLender, Lender
from onepledge_bank_agent.domain.enums import LoanState, RowDisposition, TapeStatus
from onepledge_bank_agent.domain.errors import ConcurrentUploadError
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.tape import UNFINISHED, TapeSubmission
from onepledge_bank_agent.domain.tape_file import RawRow, TapeLayout
from onepledge_bank_agent.domain.values import CheckResult


@dataclass
class MemoryState:
    """Everything a bank database would hold, as plain Python objects."""

    tapes: dict[uuid.UUID, TapeSubmission] = field(default_factory=dict)
    raw_rows: dict[uuid.UUID, list[StoredRawRow]] = field(default_factory=dict)
    layouts: dict[uuid.UUID, TapeLayout] = field(default_factory=dict)
    items: dict[uuid.UUID, list[NewItem]] = field(default_factory=dict)
    loans: dict[uuid.UUID, Loan] = field(default_factory=dict)
    checks: dict[tuple[uuid.UUID, uuid.UUID], list[CheckResult]] = field(default_factory=dict)
    tokens: set[uuid.UUID] = field(default_factory=set)
    lenders: dict[uuid.UUID, Lender] = field(default_factory=dict)
    facilities: dict[uuid.UUID, Facility] = field(default_factory=dict)
    audit: list[AuditDraft] = field(default_factory=list)


class MemoryStore:
    """The committed state, and a switch that makes the next tape insert lose a race with another upload.

    `race_next_tape_insert` makes the next insert raise `ConcurrentUploadError`; when
    `race_winner` is set, that submission is committed first, as the other transaction would.
    """

    def __init__(self) -> None:
        self.state = MemoryState()
        self.race_next_tape_insert = False
        self.race_winner: TapeSubmission | None = None
        self.commits = 0


class _Tapes:
    def __init__(self, state: MemoryState, store: MemoryStore) -> None:
        self._state = state
        self._store = store

    def add(self, tape: TapeSubmission, layout: TapeLayout) -> None:
        if self._store.race_next_tape_insert:
            self._store.race_next_tape_insert = False
            winner = self._store.race_winner
            if winner is not None:
                self._store.state.tapes[winner.submission_id] = winner
            raise ConcurrentUploadError
        self._state.tapes[tape.submission_id] = copy.deepcopy(tape)
        self._state.layouts[tape.submission_id] = layout

    def layout(self, submission_id: uuid.UUID) -> TapeLayout | None:
        return self._state.layouts.get(submission_id)

    def get(self, submission_id: uuid.UUID, *, for_update: bool = False) -> TapeSubmission | None:
        tape = self._state.tapes.get(submission_id)
        return copy.deepcopy(tape) if tape is not None else None

    def find_by_file(self, facility_id: uuid.UUID, kind: str, file_sha256: str) -> uuid.UUID | None:
        for tape in self._state.tapes.values():
            facts = tape.facts
            if (facts.facility_id, facts.kind.value, facts.file_sha256) == (facility_id, kind, file_sha256):
                return tape.submission_id
        return None

    def save(self, tape: TapeSubmission) -> None:
        self._state.tapes[tape.submission_id] = copy.deepcopy(tape)

    def runnable(self, statuses: Collection[TapeStatus], now: datetime, limit: int) -> list[uuid.UUID]:
        ordered = sorted(self._state.tapes.values(), key=lambda t: (t.facts.uploaded_at, t.submission_id))
        found = []
        for tape in ordered:
            due = tape.progress.next_attempt_at is None or tape.progress.next_attempt_at <= now
            blocked = any(
                other.facts.facility_id == tape.facts.facility_id
                and other.status in UNFINISHED
                and (other.facts.uploaded_at, other.submission_id)
                < (tape.facts.uploaded_at, tape.submission_id)
                for other in ordered
            )
            if tape.status in statuses and due and not blocked:
                found.append(tape.submission_id)
        return found[:limit]


class _RawRows:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def add_many(self, submission_id: uuid.UUID, rows: Sequence[RawRow], received_at: datetime) -> None:
        stored = [StoredRawRow(uuid.uuid4(), r.row_number, r.text, r.cells, r.row_sha256) for r in rows]
        self._state.raw_rows.setdefault(submission_id, []).extend(stored)

    def list_for(self, submission_id: uuid.UUID) -> list[StoredRawRow]:
        return sorted(self._state.raw_rows.get(submission_id, []), key=lambda r: r.row_number)


class _Items:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def add_many(self, submission_id: uuid.UUID, items: Sequence[NewItem]) -> None:
        self._state.items.setdefault(submission_id, []).extend(items)

    def accepted(self, submission_id: uuid.UUID) -> list[AcceptedItem]:
        accepted = (RowDisposition.ACCEPTED_NEW, RowDisposition.ACCEPTED_REPRESENTED)
        items = sorted(self._state.items.get(submission_id, []), key=lambda i: i.row_number)
        return [
            AcceptedItem(i.row_number, i.loan_id) for i in items if i.disposition in accepted and i.loan_id
        ]


class _Loans:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def live_by_vin(self, facility_id: uuid.UUID, vins: Iterable[str]) -> dict[str, Loan]:
        wanted = set(vins)
        return {
            loan.terms.vin.value: copy.deepcopy(loan)
            for loan in self._state.loans.values()
            if loan.record.facility_id == facility_id
            and loan.terms.vin.value in wanted
            and loan.state is not LoanState.RELEASED
        }

    def get_many(self, loan_ids: Iterable[uuid.UUID], *, for_update: bool = False) -> dict[uuid.UUID, Loan]:
        return {i: copy.deepcopy(self._state.loans[i]) for i in set(loan_ids) if i in self._state.loans}

    def add_many(self, loans: Sequence[Loan]) -> None:
        for loan in loans:
            self._state.loans[loan.loan_id] = copy.deepcopy(loan)

    def save(self, loan: Loan) -> None:
        self._state.loans[loan.loan_id] = copy.deepcopy(loan)


class _Checks:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def checked(self, submission_id: uuid.UUID, loan_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        return {i for i in loan_ids if len(self._state.checks.get((submission_id, i), [])) == 4}

    def add(
        self, submission_id: uuid.UUID, loan_id: uuid.UUID, results: Sequence[CheckResult], at: datetime
    ) -> None:
        self._state.checks[(submission_id, loan_id)] = list(results)


class _Tokens:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def loans_with_tokens(self, loan_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        return set(loan_ids) & self._state.tokens


class _Facilities:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def get(self, facility_id: uuid.UUID) -> FacilityWithLender | None:
        facility = self._state.facilities.get(facility_id)
        if facility is None:
            return None
        return FacilityWithLender(facility, self._state.lenders[facility.lender_id])

    def upsert_lender(self, lender: Lender) -> Upserted:
        existing = self._state.lenders.get(lender.lender_id)
        self._state.lenders[lender.lender_id] = lender
        return _outcome(existing, lender)

    def upsert_facility(self, facility: Facility) -> Upserted:
        existing = self._state.facilities.get(facility.facility_id)
        if existing is not None:
            facility = Facility(**{**facility.__dict__, "status": existing.status})
        self._state.facilities[facility.facility_id] = facility
        return _outcome(existing, facility)


def _outcome[T](existing: T | None, new: T) -> Upserted:
    if existing is None:
        return Upserted.INSERTED
    return Upserted.UNCHANGED if existing == new else Upserted.UPDATED


class MemoryUnitOfWork:
    """A unit of work over a copy of the store's state."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._drafts: list[AuditDraft] = []

    def __enter__(self) -> Self:
        self._state = copy.deepcopy(self._store.state)
        self.tapes = _Tapes(self._state, self._store)
        self.raw_rows = _RawRows(self._state)
        self.items = _Items(self._state)
        self.loans = _Loans(self._state)
        self.checks = _Checks(self._state)
        self.tokens = _Tokens(self._state)
        self.facilities = _Facilities(self._state)
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self._drafts.clear()

    def record(self, draft: AuditDraft) -> None:
        self._drafts.append(draft)

    def commit(self) -> None:
        self._state.audit.extend(self._drafts)
        self._drafts.clear()
        self._store.state = self._state
        self._store.commits += 1
        self._state = copy.deepcopy(self._state)
        for name in ("tapes", "raw_rows", "items", "loans", "checks", "tokens", "facilities"):
            getattr(self, name)._state = self._state


class MemoryUnits:
    """The `UnitOfWorkFactory` over one store."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def __call__(self) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(self.store)
