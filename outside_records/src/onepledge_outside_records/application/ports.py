"""Interfaces the use cases depend on; implemented in `infrastructure`."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from onepledge_outside_records.domain.records import (
    ApiClient,
    Contract,
    Payment,
    PaymentPage,
    PaymentQuery,
    RecordCounts,
    TitleRecord,
    VinText,
)


class RecordsReader(Protocol):
    """Read-only access to the records and the client list."""

    def title(self, vin: VinText) -> TitleRecord | None:
        """The title record of a VIN."""
        ...

    def payments(self, query: PaymentQuery) -> PaymentPage:
        """A page of payments ordered by paid_on descending then payment_id."""
        ...

    def contracts(self, vin: VinText) -> tuple[Contract, ...]:
        """Contract records ordered by signed_on ascending then contract_id."""
        ...

    def active_client(self, api_key_sha256: str) -> ApiClient | None:
        """The active client whose stored key hash equals this one (an indexed equality lookup)."""
        ...


@dataclass(frozen=True)
class RecordSet:
    """The three record lists of one data set."""

    titles: tuple[TitleRecord, ...]
    payments: tuple[Payment, ...]
    contracts: tuple[Contract, ...]


class RecordsSource(Protocol):
    """Where seed data comes from."""

    def load(self, data_dir: Path) -> RecordSet:
        """The records of `data_dir/outside_records/*.csv`."""
        ...


class RecordsWriter(Protocol):
    """Replaces the records (owner role only)."""

    def replace_all(self, records: RecordSet) -> RecordCounts:
        """Replace the three tables in one transaction and return the row counts."""
        ...


class ClientRegistry(Protocol):
    """Adds or updates API clients (owner role only)."""

    def upsert(self, name: str, api_key_sha256: str) -> ApiClient:
        """Create the client or replace its key, and mark it active."""
        ...
