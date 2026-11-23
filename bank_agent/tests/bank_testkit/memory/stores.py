"""In-memory implementations of the persistence ports, for unit tests of the use cases (no I/O).

A unit of work copies the store when it opens and replaces it on commit, so leaving
without commit discards everything, as a database transaction would. The audit log is
chained at commit with the domain's own chain rule (`chain_entry`), so a test can read
it back, verify it, or break it. The behaviour of every repository is pinned by the
shared port tests of `tests/ports/`, which run against these and against PostgreSQL.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

from bank_testkit.memory import guards
from onepledge_bank_agent.application.evidence_ports import IntegrityRun, ItemRecord, PlannedItem
from onepledge_bank_agent.application.ports import AcceptedItem, NewItem, StoredRawRow, Upserted
from onepledge_bank_agent.domain.alert import Alert
from onepledge_bank_agent.domain.audit import AuditDraft, AuditEntry, chain_entry
from onepledge_bank_agent.domain.entities import (
    Certificate,
    Checkpoint,
    Facility,
    FacilityWithLender,
    Lender,
    RegistryRequest,
)
from onepledge_bank_agent.domain.enums import (
    LoanState,
    QuarantineReason,
    RequestOrigin,
    RowDisposition,
    TapeStatus,
    TokenKind,
)
from onepledge_bank_agent.domain.errors import ConcurrentUploadError, ReferenceDataError
from onepledge_bank_agent.domain.integrity import DatabaseControl
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.outbox import LedgerEntry, OutboxOutcome, OutboxState
from onepledge_bank_agent.domain.raw_evidence import StoredRow, StoredTape
from onepledge_bank_agent.domain.registry_records import ItemOutcome, QuarantinedMessage, RegistryAnswer
from onepledge_bank_agent.domain.registry_results import StoredResult
from onepledge_bank_agent.domain.store_errors import UNIQUE, StoreRefusedError
from onepledge_bank_agent.domain.tape import UNFINISHED, TapeSubmission
from onepledge_bank_agent.domain.tape_file import RawRow, TapeLayout, raw_payload
from onepledge_bank_agent.domain.values import CheckResult, Token


@dataclass
class MemoryState:
    """Everything a bank database would hold, as plain Python objects."""

    tapes: dict[uuid.UUID, TapeSubmission] = field(default_factory=dict)
    raw_rows: dict[uuid.UUID, list[StoredRawRow]] = field(default_factory=dict)
    layouts: dict[uuid.UUID, TapeLayout] = field(default_factory=dict)
    items: dict[uuid.UUID, list[NewItem]] = field(default_factory=dict)
    loans: dict[uuid.UUID, Loan] = field(default_factory=dict)
    checks: dict[tuple[uuid.UUID, uuid.UUID], list[CheckResult]] = field(default_factory=dict)
    tokens: dict[uuid.UUID, list[Token]] = field(default_factory=dict)
    requests: dict[uuid.UUID, RegistryRequest] = field(default_factory=dict)
    request_items: dict[uuid.UUID, list[ItemRecord]] = field(default_factory=dict)
    answers: dict[uuid.UUID, RegistryAnswer] = field(default_factory=dict)
    certificates: list[Certificate] = field(default_factory=list)
    quarantined: list[QuarantinedMessage] = field(default_factory=list)
    alerts: dict[uuid.UUID, Alert] = field(default_factory=dict)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    integrity_runs: list[IntegrityRun] = field(default_factory=list)
    lenders: dict[uuid.UUID, Lender] = field(default_factory=dict)
    facilities: dict[uuid.UUID, Facility] = field(default_factory=dict)
    audit: list[AuditDraft] = field(default_factory=list)
    audit_log: list[AuditEntry] = field(default_factory=list)
    outbox_ledger: dict[uuid.UUID, LedgerEntry] = field(default_factory=dict)
    outbox_state: OutboxState = field(default_factory=OutboxState)


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
        facts = tape.facts
        if self.find_by_file(facts.facility_id, facts.kind.value, facts.file_sha256) is not None:
            raise ConcurrentUploadError
        guards.check_tape_insert(tape)
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
        held = self._state.tapes.get(tape.submission_id)
        if held is None:
            return
        guards.check_tape_save(held.progress, tape.progress)
        self._state.tapes[tape.submission_id] = TapeSubmission(held.facts, copy.deepcopy(tape.progress))

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
        held = {r.row_number for r in self._state.raw_rows.get(submission_id, [])}
        numbers = [r.row_number for r in rows]
        if len(set(numbers)) != len(numbers) or held & set(numbers):
            raise StoreRefusedError(UNIQUE, "23505", "uq_loans_raw_submission_row")
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

    def release_requested(self, submission_id: uuid.UUID) -> list[AcceptedItem]:
        items = sorted(self._state.items.get(submission_id, []), key=lambda i: i.row_number)
        return [
            AcceptedItem(i.row_number, i.loan_id)
            for i in items
            if i.disposition is RowDisposition.RELEASE_REQUESTED and i.loan_id
        ]

    def latest_submission_of(self, loan_id: uuid.UUID) -> uuid.UUID | None:
        found = [
            self._state.tapes[submission_id]
            for submission_id, items in self._state.items.items()
            if any(i.loan_id == loan_id for i in items) and submission_id in self._state.tapes
        ]
        latest = max(found, key=lambda t: (t.facts.uploaded_at, str(t.submission_id)), default=None)
        return latest.submission_id if latest is not None else None


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
            guards.check_loan_insert(loan)
            record = loan.record
            live = self.live_by_vin(record.facility_id, [record.terms.vin.value])
            if loan.loan_id in self._state.loans or live:
                raise StoreRefusedError(UNIQUE, "23505", "uq_loans_facility_vin_live")
            self._state.loans[loan.loan_id] = copy.deepcopy(loan)

    def save(self, loan: Loan) -> None:
        held = self._state.loans.get(loan.loan_id)
        if held is None:
            return
        guards.check_loan_save(held, loan, bool(self._state.tokens.get(loan.loan_id)))
        kept = held.record
        saved = replace(
            loan.record,
            facility_id=kept.facility_id,
            first_submission_id=kept.first_submission_id,
            created_at=kept.created_at,
        )
        self._state.loans[loan.loan_id] = Loan(saved)


class _Checks:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def checked(self, submission_id: uuid.UUID, loan_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        return {i for i in loan_ids if len(self._state.checks.get((submission_id, i), [])) == 4}

    def add(
        self, submission_id: uuid.UUID, loan_id: uuid.UUID, results: Sequence[CheckResult], at: datetime
    ) -> None:
        held = self._state.checks.get((submission_id, loan_id), [])
        kinds = [result.check_type for result in [*held, *results]]
        if len(set(kinds)) != len(kinds):
            raise StoreRefusedError(UNIQUE, "23505", "uq_local_checks_loan_submission_type")
        self._state.checks[(submission_id, loan_id)] = [*held, *results]


class _Tokens:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def loans_with_tokens(self, loan_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        return {loan_id for loan_id in loan_ids if self._state.tokens.get(loan_id)}

    def for_loans(self, loan_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, tuple[Token, ...]]:
        order = {kind: index for index, kind in enumerate(TokenKind)}
        return {
            i: tuple(sorted(self._state.tokens[i], key=lambda t: (t.key_version, order[t.kind])))
            for i in set(loan_ids)
            if self._state.tokens.get(i)
        }

    def add_many(self, tokens: Mapping[uuid.UUID, Sequence[Token]], at: datetime) -> None:
        for loan_id, loan_tokens in tokens.items():
            stored = self._state.tokens.setdefault(loan_id, [])
            keys = {(t.kind, t.key_version) for t in stored}
            stored.extend(t for t in loan_tokens if (t.kind, t.key_version) not in keys)

    def loans_with_asset_token(self, asset_token: str) -> list[uuid.UUID]:
        return sorted(
            loan_id
            for loan_id, tokens in self._state.tokens.items()
            if any(t.kind is TokenKind.ASSET and t.value.value == asset_token for t in tokens)
        )


class _Requests:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def add(self, request: RegistryRequest, items: Sequence[PlannedItem]) -> None:
        same_batch = request.origin is RequestOrigin.TAPE and any(
            r.submission_id == request.submission_id and r.origin is RequestOrigin.TAPE
            and r.batch_index == request.batch_index
            for r in self._state.requests.values()
        )  # fmt: skip
        if self.by_key(request.idempotency_key.value) is not None or same_batch:
            raise StoreRefusedError(UNIQUE, "23505", "uq_registry_requests")
        self._state.requests[request.request_id] = request
        self._state.request_items[request.request_id] = [
            ItemRecord(item.item_ref, item.position, item.loan_id, None, None) for item in items
        ]

    def for_submission(self, submission_id: uuid.UUID, origin: RequestOrigin) -> list[RegistryRequest]:
        found = [
            r
            for r in self._state.requests.values()
            if r.submission_id == submission_id and r.origin is origin
        ]
        return sorted(found, key=lambda r: (r.batch_index, str(r.request_id)))

    def get(self, request_id: uuid.UUID) -> RegistryRequest | None:
        return self._state.requests.get(request_id)

    def by_key(self, idempotency_key: str) -> RegistryRequest | None:
        return next(
            (r for r in self._state.requests.values() if r.idempotency_key.value == idempotency_key), None
        )

    def items(self, request_id: uuid.UUID) -> list[ItemRecord]:
        return sorted(self._state.request_items.get(request_id, []), key=lambda i: i.position)

    def mark_sent(self, request_id: uuid.UUID, at: datetime) -> int:
        request = self._state.requests[request_id]
        if request.response_status is not None:
            raise StoreRefusedError("OP007", "OP007", "registry_requests_guard")
        self._state.requests[request_id] = replace(request, attempts=request.attempts + 1)
        return request.attempts + 1

    def store_answer(self, request_id: uuid.UUID, answer: RegistryAnswer, at: datetime) -> None:
        request = self._state.requests[request_id]
        if request.response_status is not None:  # migration 0014: an answered request is frozen
            raise StoreRefusedError("OP007", "OP007", "registry_requests_guard")
        self._state.answers[request_id] = answer
        self._state.requests[request_id] = replace(
            request,
            response_status=answer.status,
            certificate_id=answer.certificate_id,
            registry_submission_id=answer.submission_id,
            as_of_seq=answer.as_of_seq,
        )

    def store_result(self, request_id: uuid.UUID, outcome: ItemOutcome, stored: StoredResult) -> None:
        held = [i for i in self._state.request_items[request_id] if i.item_ref == outcome.item_ref]
        if any(item.result is not None for item in held):  # migration 0014: a result is set once
            raise StoreRefusedError("OP007", "OP007", "registry_request_items_guard")
        self._state.request_items[request_id] = [
            replace(item, result=stored.result, registry_case_id=outcome.case_id)
            if item.item_ref == outcome.item_ref
            else item
            for item in self._state.request_items[request_id]
        ]


class _Certificates:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def add(self, certificate: Certificate) -> bool:
        delivery = (certificate.certificate_id, certificate.delivered_body_sha256, certificate.signature)
        held = ((c.certificate_id, c.delivered_body_sha256, c.signature) for c in self._state.certificates)
        if delivery in held:
            return False
        proven = [c for c in self._state.certificates if c.verified]
        if certificate.verified and any(
            c.certificate_id == certificate.certificate_id or c.request_id == certificate.request_id
            for c in proven
        ):
            return False
        self._state.certificates.append(certificate)
        return True

    def verified_for_requests(self, request_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Certificate]:
        wanted = set(request_ids)
        return {c.request_id: c for c in self._state.certificates if c.verified and c.request_id in wanted}

    def all(self) -> list[Certificate]:
        return sorted(self._state.certificates, key=lambda c: (c.received_at, c.certificate_row_id))

    def expected(self) -> int:
        return sum(1 for request in self._state.requests.values() if request.certificate_id is not None)

    def any_unproven(self) -> bool:
        proven = {c.request_id for c in self._state.certificates if c.verified}
        return any(not c.verified and c.request_id not in proven for c in self._state.certificates)

    def missing_for_completed(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        proven = {c.request_id for c in self._state.certificates if c.verified}
        return sorted(
            (r.request_id, r.certificate_id)
            for r in self._state.requests.values()
            if r.certificate_id is not None
            and r.request_id not in proven
            and self._state.tapes[r.submission_id].status is TapeStatus.COMPLETED
        )


class _Quarantine:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def add(self, message: QuarantinedMessage) -> bool:
        held_payload = message.quarantined
        if held_payload.reason is QuarantineReason.OVERSIZED and not held_payload.withheld:
            raise StoreRefusedError("23514", "23514", "ck_registry_messages_quarantined_payload")
        if message.message_id is None:
            key = message.quarantined.payload_sha256
            if any(
                h.message_id is None and h.quarantined.payload_sha256 == key for h in self._state.quarantined
            ):
                return False
        elif any(held.message_id == message.message_id for held in self._state.quarantined):
            return False
        self._state.quarantined.append(message)
        return True

    def count(self) -> int:
        return len(self._state.quarantined)

    def any_checkpoint(self) -> bool:
        return any(held.message_type == "CHECKPOINT" for held in self._state.quarantined)

    def all(self) -> list[QuarantinedMessage]:
        return sorted(self._state.quarantined, key=lambda held: (held.received_at, held.quarantine_id))

    def unmatched_case_updates(self, case_id: uuid.UUID, asset_token: str) -> list[QuarantinedMessage]:
        found = [
            held
            for held in self._state.quarantined
            if held.quarantined.reason is QuarantineReason.UNMATCHED
            and held.message_type == "CASE_UPDATE"
            and isinstance(payload := held.quarantined.payload, dict)
            and payload.get("case_id") == str(case_id)
            and payload.get("asset_token") == asset_token
        ]
        return sorted(found, key=lambda held: (held.received_at, held.quarantine_id), reverse=True)


class _OutboxLedger:
    """`registry_outbox_messages` with the guard of migration 0013 (SQLSTATE OP009)."""

    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def get(self, message_id: uuid.UUID) -> LedgerEntry | None:
        return self._state.outbox_ledger.get(message_id)

    def save(self, entry: LedgerEntry) -> None:
        held = self._state.outbox_ledger.get(entry.message_id)
        if held is not None:
            same = (held.message_type, held.first_seen_at) == (entry.message_type, entry.first_seen_at)
            late = held.outcome is OutboxOutcome.QUARANTINED and entry.outcome is OutboxOutcome.APPLIED_LATE
            if not same or (held.outcome is not None and entry.outcome is not held.outcome and not late):
                raise StoreRefusedError("OP009", "OP009", "registry_outbox_messages_guard")
        self._state.outbox_ledger[entry.message_id] = entry

    def applied_late(self) -> set[uuid.UUID]:
        return {
            entry.message_id
            for entry in self._state.outbox_ledger.values()
            if entry.outcome is OutboxOutcome.APPLIED_LATE
        }


class _OutboxState:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def get(self) -> OutboxState:
        return self._state.outbox_state

    def save(self, state: OutboxState) -> None:
        self._state.outbox_state = state


class _Alerts:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def get(self, alert_id: uuid.UUID, *, for_update: bool = False) -> Alert | None:
        alert = self._state.alerts.get(alert_id)
        return copy.deepcopy(alert) if alert is not None else None

    def find(self, registry_case_id: uuid.UUID, loan_id: uuid.UUID) -> Alert | None:
        return next(
            (
                copy.deepcopy(a)
                for a in self._state.alerts.values()
                if a.record.registry_case_id == registry_case_id and a.record.loan_id == loan_id
            ),
            None,
        )

    def for_case(self, registry_case_id: uuid.UUID, loan_ids: Iterable[uuid.UUID]) -> list[Alert]:
        wanted = set(loan_ids)
        found = [
            copy.deepcopy(a)
            for a in self._state.alerts.values()
            if a.record.registry_case_id == registry_case_id and a.record.loan_id in wanted
        ]
        return sorted(found, key=lambda a: a.record.alert_id)

    def for_loan(self, loan_id: uuid.UUID) -> list[Alert]:
        found = [copy.deepcopy(a) for a in self._state.alerts.values() if a.record.loan_id == loan_id]
        return sorted(found, key=lambda a: (a.record.received_at, a.record.alert_id))

    def add(self, alert: Alert) -> None:
        guards.check_alert_insert(alert)
        record = alert.record
        if record.alert_id in self._state.alerts or self.find(record.registry_case_id, record.loan_id):
            raise StoreRefusedError(UNIQUE, "23505", "uq_alerts_case_loan")
        self._state.alerts[alert.record.alert_id] = copy.deepcopy(alert)

    def save(self, alert: Alert) -> None:
        held = self._state.alerts.get(alert.record.alert_id)
        if held is None:
            return
        guards.check_alert_save(held, alert)
        kept = held.record
        saved = replace(kept, status=alert.record.status, case_status=alert.record.case_status,
                        review=alert.record.review)  # fmt: skip
        self._state.alerts[alert.record.alert_id] = Alert(saved)


class _Checkpoints:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def delivered(self, checkpoint_seq: int, head_hash: str, signature: str) -> bool:
        return any(
            (c.checkpoint_seq, c.head_hash.value, c.signature) == (checkpoint_seq, head_hash, signature)
            for c in self._state.checkpoints
        )

    def latest_verified(self) -> Checkpoint | None:
        verified = [c for c in self._state.checkpoints if c.verified]
        return max(verified, key=lambda c: c.checkpoint_seq, default=None)

    def verified_with_seq(self, checkpoint_seq: int) -> Checkpoint | None:
        return next(
            (c for c in self._state.checkpoints if c.verified and c.checkpoint_seq == checkpoint_seq), None
        )

    def add(self, checkpoint: Checkpoint) -> None:
        if self.delivered(checkpoint.checkpoint_seq, checkpoint.head_hash.value, checkpoint.signature):
            raise StoreRefusedError(UNIQUE, "23505", "uq_checkpoints_seen_delivery")
        if checkpoint.verified and self.verified_with_seq(checkpoint.checkpoint_seq) is not None:
            raise StoreRefusedError(UNIQUE, "23505", "uq_checkpoints_seen_verified_seq")
        self._state.checkpoints.append(checkpoint)

    def any_failed(self) -> bool:
        return any(not c.verified for c in self._state.checkpoints)

    def all(self) -> list[Checkpoint]:
        return sorted(
            self._state.checkpoints, key=lambda c: (c.checkpoint_seq, c.received_at, c.checkpoint_id)
        )


class _IntegrityRuns:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def add(self, run: IntegrityRun) -> None:
        self._state.integrity_runs.append(run)

    def ids(self) -> set[uuid.UUID]:
        return {run.run_id for run in self._state.integrity_runs}

    def latest(self) -> IntegrityRun | None:
        runs = sorted(self._state.integrity_runs, key=lambda run: run.run_id)
        return max(runs, key=lambda run: run.ran_at, default=None)


AUDIT_TIME = datetime(2026, 11, 20, tzinfo=UTC)


class _EvidenceStores:
    """The four insert-only proof stores, over the current state."""

    def __init__(self, state: MemoryState) -> None:
        self._state = state

    @property
    def certificates(self) -> _Certificates:
        return _Certificates(self._state)

    @property
    def checkpoints(self) -> _Checkpoints:
        return _Checkpoints(self._state)

    @property
    def quarantine(self) -> _Quarantine:
        return _Quarantine(self._state)

    @property
    def runs(self) -> _IntegrityRuns:
        return _IntegrityRuns(self._state)


class _History:
    """The stored tapes, and the committed drafts chained the way the SQL writer chains them."""

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
            facts.submission_id, facts.source, facts.facility_id, facts.as_of_date, facts.file_sha256,
            facts.rows_sha256, facts.row_count, layout, rows,
        )  # fmt: skip


class MemoryControls:
    """Database controls as set by the test; all in force by default."""

    def __init__(self) -> None:
        self.state = dict.fromkeys(DatabaseControl, True)
        self.role_violations: tuple[str, ...] = ()

    def controls(self) -> Mapping[DatabaseControl, bool]:
        return dict(self.state)

    def violations(self) -> tuple[str, ...]:
        return self.role_violations


CONFLICT = "The reference data conflicts with a lender or facility already stored under another id."


class _Facilities:
    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def get(self, facility_id: uuid.UUID) -> FacilityWithLender | None:
        facility = self._state.facilities.get(facility_id)
        if facility is None:
            return None
        return FacilityWithLender(facility, self._state.lenders[facility.lender_id])

    def upsert_lender(self, lender: Lender) -> Upserted:
        others = (held for held in self._state.lenders.values() if held.lender_id != lender.lender_id)
        if any(held.lei == lender.lei or held.legal_name == lender.legal_name for held in others):
            raise ReferenceDataError(CONFLICT)
        existing = self._state.lenders.get(lender.lender_id)
        self._state.lenders[lender.lender_id] = lender
        return _outcome(existing, lender)

    def upsert_facility(self, facility: Facility) -> Upserted:
        others = (
            held for held in self._state.facilities.values() if held.facility_id != facility.facility_id
        )
        if any(held.code == facility.code for held in others):
            raise ReferenceDataError(CONFLICT)
        existing = self._state.facilities.get(facility.facility_id)
        if existing is not None:
            facility = Facility(**{**facility.__dict__, "status": existing.status})
        self._state.facilities[facility.facility_id] = facility
        return _outcome(existing, facility)


def _outcome[T](existing: T | None, new: T) -> Upserted:
    if existing is None:
        return Upserted.INSERTED
    return Upserted.UNCHANGED if existing == new else Upserted.UPDATED


REPOSITORIES = (
    "tapes", "raw_rows", "items", "loans", "checks", "tokens", "facilities", "requests", "alerts",
    "outbox_ledger", "outbox_state", "evidence", "history",
)  # fmt: skip


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
        self.requests = _Requests(self._state)
        self.alerts = _Alerts(self._state)
        self.outbox_ledger = _OutboxLedger(self._state)
        self.outbox_state = _OutboxState(self._state)
        self.evidence = _EvidenceStores(self._state)
        self.history = _History(self._state)
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self._drafts.clear()

    def record(self, draft: AuditDraft) -> None:
        self._drafts.append(draft)

    def commit(self) -> None:
        self._state.audit.extend(self._drafts)
        for draft in self._drafts:
            last = self._state.audit_log[-1] if self._state.audit_log else None
            previous = (last.seq, last.entry_hash) if last is not None else None
            audit_id = uuid.UUID(int=len(self._state.audit_log) + 1, version=4)
            self._state.audit_log.append(chain_entry(draft, audit_id, AUDIT_TIME, previous))
        self._drafts.clear()
        self._store.state = self._state
        self._store.commits += 1
        self._state = copy.deepcopy(self._state)
        for name in REPOSITORIES:
            getattr(self, name)._state = self._state


class MemoryUnits:
    """The `UnitOfWorkFactory` over one store."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def __call__(self) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(self.store)
