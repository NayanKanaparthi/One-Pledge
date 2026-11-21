"""The three lookups of `contracts/outside_records.yaml`, and client authentication (SPEC 6.2)."""

from __future__ import annotations

import hashlib
import hmac

from onepledge_outside_records.application.ports import RecordsReader
from onepledge_outside_records.domain.errors import (
    ContractNotFoundError,
    TitleNotFoundError,
    UnauthenticatedError,
)
from onepledge_outside_records.domain.records import (
    ApiClient,
    Contract,
    PaymentPage,
    PaymentQuery,
    TitleRecord,
    VinText,
)


class AuthenticateClient:
    """Finds the active client of an `X-API-Key` by its SHA-256."""

    def __init__(self, reader: RecordsReader) -> None:
        self._reader = reader

    def run(self, api_key: str | None) -> ApiClient:
        """The client, or UnauthenticatedError for a missing, unknown or inactive key."""
        if not api_key:
            raise UnauthenticatedError
        presented = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        found = self._reader.active_client(presented)
        if found is None or not hmac.compare_digest(found[1], presented):
            raise UnauthenticatedError
        return found[0]


class GetTitle:
    """`GET /v1/titles/{vin}`."""

    def __init__(self, reader: RecordsReader) -> None:
        self._reader = reader

    def run(self, vin: VinText) -> TitleRecord:
        """The title record, or TitleNotFoundError."""
        record = self._reader.title(vin)
        if record is None:
            raise TitleNotFoundError
        return record


class ListPayments:
    """`GET /v1/payments`."""

    def __init__(self, reader: RecordsReader) -> None:
        self._reader = reader

    def run(self, query: PaymentQuery) -> PaymentPage:
        """A page of payments, possibly empty."""
        return self._reader.payments(query)


class GetContracts:
    """`GET /v1/contracts/{vin}`."""

    def __init__(self, reader: RecordsReader) -> None:
        self._reader = reader

    def run(self, vin: VinText) -> tuple[Contract, ...]:
        """At least one contract, or ContractNotFoundError."""
        contracts = self._reader.contracts(vin)
        if not contracts:
            raise ContractNotFoundError
        return contracts
