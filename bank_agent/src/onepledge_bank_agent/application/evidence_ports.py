"""Repository ports of the registry side and of the bank's evidence (SPEC 12.1, 13 to 15).

`registry_requests` with their items, `certificates`, `alerts`, `checkpoints_seen`,
`registry_messages_quarantined`, `integrity_runs`, reads of `audit_log`, the stored
forms of every tape for deep verification, and the live database controls. The unit
of work exposes all of them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from onepledge_bank_agent.domain.alert import Alert
from onepledge_bank_agent.domain.audit import AuditEntry
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.entities import Certificate, Checkpoint, RegistryRequest
from onepledge_bank_agent.domain.enums import IntegrityRunStatus, RegistryItemResult, RequestOrigin
from onepledge_bank_agent.domain.integrity import DatabaseControl
from onepledge_bank_agent.domain.outbox import LedgerEntry, OutboxState
from onepledge_bank_agent.domain.raw_evidence import StoredTape
from onepledge_bank_agent.domain.registry_records import ItemOutcome, QuarantinedMessage, RegistryAnswer
from onepledge_bank_agent.domain.registry_results import StoredResult
from onepledge_bank_agent.domain.replay import State
from onepledge_bank_agent.domain.stage_digests import DigestRow, StagePart


@dataclass(frozen=True)
class PlannedItem:
    """One item of a planned request: a fresh reference, its position and its loan."""

    item_ref: uuid.UUID
    position: int
    loan_id: uuid.UUID


@dataclass(frozen=True)
class ItemRecord:
    """A stored request item and, once answered, its result and case."""

    item_ref: uuid.UUID
    position: int
    loan_id: uuid.UUID
    result: RegistryItemResult | None
    registry_case_id: uuid.UUID | None


class RegistryRequestRepository(Protocol):
    """`registry_requests` and `registry_request_items`."""

    def add(self, request: RegistryRequest, items: Sequence[PlannedItem]) -> None:
        """Insert a planned request and its items."""
        ...

    def for_submission(self, submission_id: uuid.UUID, origin: RequestOrigin) -> list[RegistryRequest]:
        """A submission's requests of one origin, in batch order."""
        ...

    def get(self, request_id: uuid.UUID) -> RegistryRequest | None:
        """One request."""
        ...

    def by_key(self, idempotency_key: str) -> RegistryRequest | None:
        """The request carrying this idempotency key."""
        ...

    def items(self, request_id: uuid.UUID) -> list[ItemRecord]:
        """The items of a request in position order."""
        ...

    def mark_sent(self, request_id: uuid.UUID, at: datetime) -> int:
        """Count one more attempt (setting `sent_at` on the first); returns the attempt count."""
        ...

    def store_answer(self, request_id: uuid.UUID, answer: RegistryAnswer, at: datetime) -> None:
        """Store the response facts and body (codes only)."""
        ...

    def store_result(self, request_id: uuid.UUID, outcome: ItemOutcome, stored: StoredResult) -> None:
        """Store one item's result."""
        ...


class CertificateRepository(Protocol):
    """`certificates` (insert-only): every certificate received, failed ones kept (SPEC_DECISIONS 61)."""

    def add(self, certificate: Certificate) -> bool:
        """Store a received certificate with its verdict; False, storing nothing, when this exact
        delivery (certificate id, delivered body SHA-256 and signature) is already stored."""
        ...

    def verified_for_requests(self, request_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Certificate]:
        """The verified certificate of each of these requests that has one, by request id."""
        ...

    def all(self) -> list[Certificate]:
        """Every stored row, verified or failed, oldest first."""
        ...

    def expected(self) -> int:
        """The number of answered registry requests, each of which should have a certificate."""
        ...

    def any_unproven(self) -> bool:
        """True when some request has a failed certificate row and no verified one."""
        ...

    def missing_for_completed(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """`(request_id, certificate_id)` of every answered request of a COMPLETED tape that has no
        verified certificate row: the certificate the registry announced (SPEC_DECISIONS 89 a)."""
        ...


class QuarantineRepository(Protocol):
    """`registry_messages_quarantined` (insert-only, SPEC_DECISIONS 57, 101)."""

    def add(self, message: QuarantinedMessage) -> bool:
        """Store a quarantined message; False, storing nothing, when it is already held: by its message id,
        or for an element without a usable id by its payload hash."""
        ...

    def count(self) -> int:
        """The number of quarantined messages."""
        ...

    def any_checkpoint(self) -> bool:
        """True when a message of type CHECKPOINT is quarantined."""
        ...

    def all(self) -> list[QuarantinedMessage]:
        """Every quarantined message, oldest first."""
        ...

    def unmatched_case_updates(self, case_id: uuid.UUID, asset_token: str) -> list[QuarantinedMessage]:
        """The CASE_UPDATE messages quarantined UNMATCHED for this case and asset code, newest first."""
        ...


class OutboxLedgerRepository(Protocol):
    """`registry_outbox_messages` (SPEC_DECISIONS 101): a row per message with a usable id, never deleted."""

    def get(self, message_id: uuid.UUID) -> LedgerEntry | None:
        """The message's row, if it was seen."""
        ...

    def save(self, entry: LedgerEntry) -> None:
        """Insert or update the row; the guard keeps id, type and first sight, and the outcome set once."""
        ...

    def applied_late(self) -> set[uuid.UUID]:
        """The message ids whose quarantined case update was applied late (their rows are not open)."""
        ...


class OutboxStateRepository(Protocol):
    """`registry_outbox_state`: the single row of completeness and blindness (SPEC_DECISIONS 101)."""

    def get(self) -> OutboxState:
        """The state; the initial one (never complete) when no sync has ended yet."""
        ...

    def save(self, state: OutboxState) -> None:
        """Replace the state."""
        ...


class AlertRepository(Protocol):
    """`alerts`."""

    def get(self, alert_id: uuid.UUID, *, for_update: bool = False) -> Alert | None:
        """One alert."""
        ...

    def find(self, registry_case_id: uuid.UUID, loan_id: uuid.UUID) -> Alert | None:
        """The alert of a case on a loan."""
        ...

    def for_case(self, registry_case_id: uuid.UUID, loan_ids: Iterable[uuid.UUID]) -> list[Alert]:
        """The alerts of a case on any of these loans."""
        ...

    def for_loan(self, loan_id: uuid.UUID) -> list[Alert]:
        """Every alert on a loan."""
        ...

    def add(self, alert: Alert) -> None:
        """Insert a new alert; refused: not NEW with an OPEN case (OP006), a second one of a case and a
        loan (UNIQUE)."""
        ...

    def save(self, alert: Alert) -> None:
        """Store the review fields and statuses of an alert. Refused (OP006): a transition outside SPEC 8.5,
        a status that does not match its case, a second position, any change of a closed alert."""
        ...


class CheckpointRepository(Protocol):
    """`checkpoints_seen` (insert-only): every checkpoint received, failed ones kept as evidence."""

    def delivered(self, checkpoint_seq: int, head_hash: str, signature: str) -> bool:
        """A row with exactly this seq, head and signature exists (a duplicate delivery)."""
        ...

    def latest_verified(self) -> Checkpoint | None:
        """The verified checkpoint with the highest seq."""
        ...

    def verified_with_seq(self, checkpoint_seq: int) -> Checkpoint | None:
        """The verified checkpoint of this seq."""
        ...

    def add(self, checkpoint: Checkpoint) -> None:
        """Store a checkpoint with its verdict; the same delivery twice, or a second verified checkpoint
        of a seq, is refused (UNIQUE)."""
        ...

    def any_failed(self) -> bool:
        """At least one stored checkpoint failed verification."""
        ...

    def all(self) -> list[Checkpoint]:
        """Every stored checkpoint, by seq then receipt."""
        ...


@dataclass(frozen=True)
class IntegrityRun:
    """One deep verification and the report it produced."""

    run_id: uuid.UUID
    ran_at: datetime
    triggered_by: uuid.UUID | None
    status: IntegrityRunStatus
    report: Mapping[str, JsonValue]


class IntegrityRunRepository(Protocol):
    """`integrity_runs` (insert-only)."""

    def add(self, run: IntegrityRun) -> None:
        """Store a deep verification."""
        ...

    def latest(self) -> IntegrityRun | None:
        """The most recent deep verification."""
        ...

    def ids(self) -> set[uuid.UUID]:
        """The id of every stored deep verification."""
        ...


class EvidenceStores(Protocol):
    """The insert-only proof stores and nothing else (SPEC_DECISIONS 67): what the registry signed and
    the bank verified, the registry messages it quarantined, and its own deep verifications."""

    @property
    def certificates(self) -> CertificateRepository:
        """`certificates`: every certificate received, with the bank's verdict."""
        ...

    @property
    def checkpoints(self) -> CheckpointRepository:
        """`checkpoints_seen`: every signed checkpoint received, with the bank's verdict."""
        ...

    @property
    def quarantine(self) -> QuarantineRepository:
        """`registry_messages_quarantined`: known messages whose payload broke the contract."""
        ...

    @property
    def runs(self) -> IntegrityRunRepository:
        """`integrity_runs`: the deep verifications and their reports."""
        ...


class HistoryReader(Protocol):
    """The audit log and the stored forms of every tape, as deep verification reads them.

    Audit entries are appended through the unit of work; these are reads only.
    """

    def audit_entries(self) -> Iterator[AuditEntry]:
        """Every audit entry in seq order."""
        ...

    def audit_size(self) -> tuple[int, int]:
        """The number of audit entries and the highest seq (0 and 0 when empty)."""
        ...

    def submission_ids(self) -> list[uuid.UUID]:
        """Every submission in upload order."""
        ...

    def tape(self, submission_id: uuid.UUID) -> StoredTape | None:
        """One submission's facts, layout and raw rows."""
        ...

    def loan_ids(self) -> set[uuid.UUID]:
        """The id of every loan."""
        ...

    def stage_rows(self, submission_id: uuid.UUID, part: StagePart) -> list[DigestRow]:
        """The rows one stage digest of a submission covers, as stored (SPEC_DECISIONS 107 a)."""
        ...

    def request_rows(self, request_ids: Sequence[uuid.UUID]) -> list[DigestRow]:
        """These registry requests with their answers and items, as stored."""
        ...

    def reference_rows(self) -> dict[str, list[DigestRow]]:
        """`lenders` and `facilities`, as stored."""
        ...

    def states(self, table: str) -> dict[uuid.UUID, State]:
        """The columns the audit replay follows, for `loans`, `tape_submissions`, `alerts` or `users`."""
        ...

    def loan_values(self) -> dict[uuid.UUID, DigestRow]:
        """Each loan's vin, borrower, origination date, amount, balance and lender loan number."""
        ...


class DatabaseControlInspector(Protocol):
    """Reads the catalogue to show which database controls are in force right now (SPEC 12.4 point 6)."""

    def controls(self) -> Mapping[DatabaseControl, bool]:
        """Each control and whether it holds."""
        ...

    def violations(self) -> tuple[str, ...]:
        """How the app role differs from the grant matrix, as `CODE on object` (SPEC_DECISIONS 72)."""
        ...
