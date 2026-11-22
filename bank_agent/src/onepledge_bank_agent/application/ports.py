"""The interfaces the use cases depend on (SPEC 8.6). Infrastructure implements them.

A unit of work is one database transaction exposing every repository. Use cases record
audit entries on it; the unit of work appends them, hash chained, as the last
statements before commit (SPEC 17.1).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self

from onepledge_bank_agent.application.evidence_ports import (
    AlertRepository,
    CertificateRepository,
    CheckpointRepository,
    EvidenceReader,
    IntegrityRunRepository,
    RegistryRequestRepository,
)
from onepledge_bank_agent.domain.audit import AuditDraft
from onepledge_bank_agent.domain.entities import Facility, FacilityWithLender, Lender
from onepledge_bank_agent.domain.enums import RowDisposition, TapeStatus
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.outside_records import ContractRecord, PaymentWindow, TitleRecord
from onepledge_bank_agent.domain.tape import TapeSubmission
from onepledge_bank_agent.domain.tape_file import RawRow, TapeLayout
from onepledge_bank_agent.domain.values import CheckResult, CleaningIssue, Token
from onepledge_bank_agent.domain.vin import Vin


class Clock(Protocol):
    """The time. Use cases read the current time only through this port."""

    def now(self) -> datetime:
        """Timezone aware UTC now."""
        ...


class IdGenerator(Protocol):
    """New identifiers."""

    def new_id(self) -> uuid.UUID:
        """A fresh UUID version 4."""
        ...


class Upserted(StrEnum):
    """What an upsert did to the row with that id."""

    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class StoredRawRow:
    """A raw row as stored in `loans_raw`: its verbatim text and its cells in file order."""

    raw_id: uuid.UUID
    row_number: int
    text: str
    cells: tuple[str, ...]
    row_sha256: str


@dataclass(frozen=True)
class NewItem:
    """One `submission_items` row and its cleaning issues, written by the clean stage."""

    item_id: uuid.UUID
    raw_id: uuid.UUID
    row_number: int
    disposition: RowDisposition
    loan_id: uuid.UUID | None
    issues: tuple[CleaningIssue, ...]


@dataclass(frozen=True)
class AcceptedItem:
    """An item whose loan the check stage examines."""

    row_number: int
    loan_id: uuid.UUID


class TapeRepository(Protocol):
    """`tape_submissions`."""

    def add(self, tape: TapeSubmission, layout: TapeLayout) -> None:
        """Insert a new submission; raises `ConcurrentUploadError` when the same file was stored meanwhile."""
        ...

    def layout(self, submission_id: uuid.UUID) -> TapeLayout | None:
        """The verbatim header, BOM flag and trailer stored with a submission."""
        ...

    def get(self, submission_id: uuid.UUID, *, for_update: bool = False) -> TapeSubmission | None:
        """One submission, optionally locked for the rest of the transaction."""
        ...

    def find_by_file(self, facility_id: uuid.UUID, kind: str, file_sha256: str) -> uuid.UUID | None:
        """The submission already holding this file for this facility and kind."""
        ...

    def save(self, tape: TapeSubmission) -> None:
        """Store the progress fields of a submission."""
        ...

    def runnable(self, statuses: Collection[TapeStatus], now: datetime, limit: int) -> list[uuid.UUID]:
        """Submissions the worker may advance now, in upload order, one facility's tapes strictly in order."""
        ...


class RawRowRepository(Protocol):
    """`loans_raw` (insert-only)."""

    def add_many(self, submission_id: uuid.UUID, rows: Sequence[RawRow], received_at: datetime) -> None:
        """Store every raw row of an upload."""
        ...

    def list_for(self, submission_id: uuid.UUID) -> list[StoredRawRow]:
        """The raw rows of a submission in row number order."""
        ...


class SubmissionItemRepository(Protocol):
    """`submission_items` and `cleaning_issues` (both insert-only)."""

    def add_many(self, submission_id: uuid.UUID, items: Sequence[NewItem]) -> None:
        """Store every item of a submission with its issues."""
        ...

    def accepted(self, submission_id: uuid.UUID) -> list[AcceptedItem]:
        """Items with disposition ACCEPTED_NEW or ACCEPTED_REPRESENTED, in row number order."""
        ...

    def release_requested(self, submission_id: uuid.UUID) -> list[AcceptedItem]:
        """Items with disposition RELEASE_REQUESTED, in row number order."""
        ...

    def latest_submission_of(self, loan_id: uuid.UUID) -> uuid.UUID | None:
        """The most recently uploaded submission that has an item for this loan."""
        ...


class LoanRepository(Protocol):
    """`loans`."""

    def live_by_vin(self, facility_id: uuid.UUID, vins: Iterable[str]) -> dict[str, Loan]:
        """The facility's loans that are not RELEASED, by VIN."""
        ...

    def get_many(self, loan_ids: Iterable[uuid.UUID], *, for_update: bool = False) -> dict[uuid.UUID, Loan]:
        """Loans by id."""
        ...

    def add_many(self, loans: Sequence[Loan]) -> None:
        """Insert new loan rows."""
        ...

    def save(self, loan: Loan) -> None:
        """Store a loan's state, terms, loan number and balance."""
        ...


class CheckRepository(Protocol):
    """`local_checks` (insert-only)."""

    def checked(self, submission_id: uuid.UUID, loan_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """The loans that already have all four check rows for this submission."""
        ...

    def add(
        self, submission_id: uuid.UUID, loan_id: uuid.UUID, results: Sequence[CheckResult], at: datetime
    ) -> None:
        """Store the four results of one loan."""
        ...


class TokenRepository(Protocol):
    """`loan_tokens` (insert-only)."""

    def loans_with_tokens(self, loan_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
        """The loans that have at least one stored token, of any key version."""
        ...

    def for_loans(self, loan_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, tuple[Token, ...]]:
        """Every stored token of these loans, of every key version."""
        ...

    def add_many(self, tokens: Mapping[uuid.UUID, Sequence[Token]], at: datetime) -> None:
        """Insert tokens; a row that already exists is left as it is (`ON CONFLICT DO NOTHING`)."""
        ...

    def loans_with_asset_token(self, asset_token: str) -> list[uuid.UUID]:
        """The loans whose ASSET token is this code."""
        ...


class FacilityRepository(Protocol):
    """`lenders` and `facilities`."""

    def get(self, facility_id: uuid.UUID) -> FacilityWithLender | None:
        """A facility with its lender."""
        ...

    def upsert_lender(self, lender: Lender) -> Upserted:
        """Insert or update a lender by id (reference seeding, owner role); says which happened."""
        ...

    def upsert_facility(self, facility: Facility) -> Upserted:
        """Insert or update a facility by id, keeping its status (reference seeding, owner role)."""
        ...


class UnitOfWork(Protocol):
    """One database transaction. Leaving the block without `commit()` rolls everything back."""

    @property
    def tapes(self) -> TapeRepository:
        """Submissions."""
        ...

    @property
    def raw_rows(self) -> RawRowRepository:
        """Raw rows."""
        ...

    @property
    def items(self) -> SubmissionItemRepository:
        """Submission items and cleaning issues."""
        ...

    @property
    def loans(self) -> LoanRepository:
        """Loans."""
        ...

    @property
    def checks(self) -> CheckRepository:
        """Local check results."""
        ...

    @property
    def tokens(self) -> TokenRepository:
        """Loan tokens."""
        ...

    @property
    def facilities(self) -> FacilityRepository:
        """Lenders and facilities."""
        ...

    @property
    def requests(self) -> RegistryRequestRepository:
        """Registry requests and their items."""
        ...

    @property
    def certificates(self) -> CertificateRepository:
        """Stored certificates."""
        ...

    @property
    def alerts(self) -> AlertRepository:
        """Alerts."""
        ...

    @property
    def checkpoints(self) -> CheckpointRepository:
        """Checkpoints received."""
        ...

    @property
    def integrity_runs(self) -> IntegrityRunRepository:
        """Deep verifications."""
        ...

    @property
    def evidence(self) -> EvidenceReader:
        """The stored forms of every tape and reads of the audit log."""
        ...

    def __enter__(self) -> Self:
        """Open the transaction."""
        ...

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        """Roll back unless committed, and release the connection."""
        ...

    def record(self, draft: AuditDraft) -> None:
        """Queue an audit entry; it is written, hash chained, just before commit."""
        ...

    def commit(self) -> None:
        """Append the queued audit entries and commit."""
        ...


type UnitOfWorkFactory = Callable[[], UnitOfWork]


class TitleRecordsClient(Protocol):
    """Outside records title lookups."""

    def title(self, vin: Vin) -> TitleRecord | None:
        """The title record, or None when the title office holds none (404 TITLE_NOT_FOUND)."""
        ...


class PaymentsClient(Protocol):
    """Outside records payment lookups."""

    def payments(self, vin: Vin, window_from: date, window_to: date) -> PaymentWindow:
        """Payments received in the inclusive window, newest first."""
        ...


class ContractsClient(Protocol):
    """Outside records contract vault lookups."""

    def contracts(self, vin: Vin) -> tuple[ContractRecord, ...]:
        """Every contract for the vehicle (empty for 404 CONTRACT_NOT_FOUND)."""
        ...


class SubmissionLocks(Protocol):
    """Session level advisory locks, one per submission (SPEC 13.1 step 3)."""

    def hold(self, submission_id: uuid.UUID) -> AbstractContextManager[bool]:
        """Try to take the lock; the context yields whether it was granted and releases it on exit."""
        ...


class StopSignal(Protocol):
    """Set when the process is asked to stop."""

    def is_set(self) -> bool:
        """True once a stop was requested."""
        ...

    def wait(self, seconds: float) -> bool:
        """Sleep up to `seconds`, waking early on a stop; True when a stop was requested."""
        ...


type LogValue = str | int | bool | None


class OperationalLog(Protocol):
    """Structured operational logging. Event names come from `LogEvent`; values are ids and codes only."""

    def info(self, event: str, **fields: LogValue) -> None:
        """An informational event."""
        ...

    def warning(self, event: str, **fields: LogValue) -> None:
        """Something an operator should look at."""
        ...

    def failure(self, event: str, error: BaseException, **fields: LogValue) -> None:
        """An unexpected error, logged with its type and frame locations only."""
        ...


@dataclass(frozen=True)
class ReferenceData:
    """One bank's lenders and facilities, as read from a reference data file, and that file's SHA-256."""

    lenders: tuple[Lender, ...]
    facilities: tuple[Facility, ...]
    source_sha256: str


class ReferenceSource(Protocol):
    """Reads one bank's reference data from the file datagen writes for seeding."""

    def load(self, data_dir: Path, bank_code: str) -> ReferenceData:
        """The lenders and facilities of one bank; raises `ReferenceDataError` when the file is unusable."""
        ...
