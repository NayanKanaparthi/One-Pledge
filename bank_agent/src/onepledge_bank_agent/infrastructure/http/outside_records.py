"""Clients of the outside records service: titles, payments and contracts (`contracts/outside_records.yaml`).

Responses are parsed into models that ignore unknown fields (additive evolution) and
enforce the required ones. A 404 means "no record" only when the error code says so;
every other failure is classified by `classification.py` (SPEC_DECISIONS 49), and an
answer outside the contract or about another vehicle is MALFORMED_RESPONSE.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from onepledge_bank_agent.domain.errors import OutsideRecordsProtocolError
from onepledge_bank_agent.domain.outside_records import (
    ContractRecord,
    PaymentRecord,
    PaymentWindow,
    TitleRecord,
)
from onepledge_bank_agent.domain.vin import Vin
from onepledge_bank_agent.infrastructure.http.base import RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.classification import failure

TITLE_ROUTE = "/v1/titles/{vin}"
PAYMENTS_ROUTE = "/v1/payments"
CONTRACTS_ROUTE = "/v1/contracts/{vin}"
PAYMENTS_PAGE_LIMIT = 200
VIN_PATTERN = r"^[A-HJ-NPR-Z0-9]{17}$"


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _TitleBody(_Lenient):
    vin: str = Field(pattern=VIN_PATTERN)
    state: str = Field(pattern=r"^[A-Z]{2}$")
    title_status: Literal["CLEAN", "SALVAGE", "JUNK"]
    lienholder_name: str | None = Field(max_length=200)
    lien_recorded_on: date | None


class _PaymentBody(_Lenient):
    payment_id: uuid.UUID
    vin: str = Field(pattern=VIN_PATTERN)
    paid_on: date
    amount_cents: int = Field(ge=1)
    deposit_bank_code: str = Field(pattern=r"^[A-Z]{3,12}$")


class _PaymentPageBody(_Lenient):
    items: list[_PaymentBody]
    total: int = Field(ge=0)


class _ContractBody(_Lenient):
    contract_id: uuid.UUID
    vin: str = Field(pattern=VIN_PATTERN)
    lender_name: str = Field(max_length=200)
    amount_cents: int = Field(ge=1)
    signed_on: date


class _ContractListBody(_Lenient):
    vin: str = Field(pattern=VIN_PATTERN)
    contracts: list[_ContractBody] = Field(min_length=1)


class _ErrorCode(_Lenient):
    code: str


class _ErrorBody(_Lenient):
    error: _ErrorCode


def _not_found(response: httpx.Response, code: str) -> bool:
    if response.status_code != httpx.codes.NOT_FOUND:
        return False
    try:
        return _ErrorBody.model_validate_json(response.content).error.code == code
    except ValidationError:
        return False


def _parse[M: BaseModel](model: type[M], response: httpx.Response) -> M:
    try:
        return model.model_validate_json(response.content)
    except ValidationError:
        raise OutsideRecordsProtocolError(response.status_code) from None


class OutsideRecordsClient:
    """Implements the `TitleRecordsClient`, `PaymentsClient` and `ContractsClient` ports."""

    def __init__(self, caller: RetryingHttpCaller) -> None:
        self._caller = caller

    def _failure(self, response: httpx.Response) -> Exception:
        """A 404 that is not an agreed "no record" means the call was about something else: malformed."""
        if response.status_code == httpx.codes.NOT_FOUND:
            return OutsideRecordsProtocolError(response.status_code)
        return failure(response, self._caller.errors)

    def title(self, vin: Vin) -> TitleRecord | None:
        """`GET /v1/titles/{vin}`: the record, or None for 404 TITLE_NOT_FOUND."""
        response = self._caller.get(TITLE_ROUTE, f"/v1/titles/{vin.value}")
        if _not_found(response, "TITLE_NOT_FOUND"):
            return None
        if response.status_code != httpx.codes.OK:
            raise self._failure(response)
        body = _parse(_TitleBody, response)
        if body.vin != vin.value:
            raise OutsideRecordsProtocolError(response.status_code)
        return TitleRecord(body.state, body.title_status, body.lienholder_name, body.lien_recorded_on)

    def payments(self, vin: Vin, window_from: date, window_to: date) -> PaymentWindow:
        """`GET /v1/payments?vin&from&to&limit=200`: payments in the inclusive window, newest first."""
        params: dict[str, str | int] = {
            "vin": vin.value,
            "from": window_from.isoformat(),
            "to": window_to.isoformat(),
            "limit": PAYMENTS_PAGE_LIMIT,
        }
        response = self._caller.get(PAYMENTS_ROUTE, "/v1/payments", params)
        if response.status_code != httpx.codes.OK:
            raise self._failure(response)
        body = _parse(_PaymentPageBody, response)
        if any(item.vin != vin.value for item in body.items):
            raise OutsideRecordsProtocolError(response.status_code)
        payments = tuple(
            PaymentRecord(item.payment_id, item.paid_on, item.amount_cents, item.deposit_bank_code)
            for item in body.items
        )
        return PaymentWindow(window_from, window_to, payments, body.total)

    def contracts(self, vin: Vin) -> tuple[ContractRecord, ...]:
        """`GET /v1/contracts/{vin}`: every contract, or none for 404 CONTRACT_NOT_FOUND."""
        response = self._caller.get(CONTRACTS_ROUTE, f"/v1/contracts/{vin.value}")
        if _not_found(response, "CONTRACT_NOT_FOUND"):
            return ()
        if response.status_code != httpx.codes.OK:
            raise self._failure(response)
        body = _parse(_ContractListBody, response)
        if body.vin != vin.value or any(item.vin != vin.value for item in body.contracts):
            raise OutsideRecordsProtocolError(response.status_code)
        return tuple(
            ContractRecord(item.contract_id, item.lender_name, item.amount_cents, item.signed_on)
            for item in body.contracts
        )
