"""The three lookups of `contracts/outside_records.yaml`, and client authentication (SPEC 6.2)."""

from __future__ import annotations

import hashlib

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
    """Finds the active client of an `X-API-Key`.

    The stored value is the SHA-256 of a 256 bit random key, and the lookup is an indexed
    equality on that hash in the database. Nothing is compared in memory, so there is no
    comparison here to make constant time; an attacker who can time the lookup learns at
    most about hashes of guesses, which reveals nothing usable about a 256 bit key.
    """

    def __init__(self, reader: RecordsReader) -> None:
        self._reader = reader

    def run(self, api_key: str | None) -> ApiClient:
        """The client, or UnauthenticatedError for a missing, unknown or inactive key."""
        if not api_key:
            raise UnauthenticatedError
        found = self._reader.active_client(hashlib.sha256(api_key.encode("utf-8")).hexdigest())
        if found is None:
            raise UnauthenticatedError
        return found


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
