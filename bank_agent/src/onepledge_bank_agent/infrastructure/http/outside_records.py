"""Clients of the outside records service: titles, payments and contracts (`contracts/outside_records.yaml`).

Responses are read with the bounds of every answer (`answers.plain_json`, SPEC_DECISIONS
74), then parsed into models that ignore unknown fields (additive evolution) and enforce
the required ones. A 404 means "no record" only when the error code says so;
every other failure is classified by `classification.py` (SPEC_DECISIONS 49), and an
answer outside the contract or about another vehicle is MALFORMED_RESPONSE.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

import httpx
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from onepledge_bank_agent.domain.errors import OutsideRecordsProtocolError
from onepledge_bank_agent.domain.outside_records import (
    ContractRecord,
    PaymentRecord,
    PaymentWindow,
    TitleRecord,
)
from onepledge_bank_agent.domain.storable import BIGINT_MAX, EARLIEST_DATE, LATEST_DATE
from onepledge_bank_agent.domain.vin import Vin
from onepledge_bank_agent.infrastructure.http.answers import CanonicalUuid, plain_json
from onepledge_bank_agent.infrastructure.http.base import RetryingHttpCaller
from onepledge_bank_agent.infrastructure.http.classification import failure

TITLE_ROUTE = "/v1/titles/{vin}"
PAYMENTS_ROUTE = "/v1/payments"
CONTRACTS_ROUTE = "/v1/contracts/{vin}"
PAYMENTS_PAGE_LIMIT = 200
MAX_PAYMENTS_IN_WINDOW = 10_000
VIN_PATTERN = r"^[A-HJ-NPR-Z0-9]{17}$"


def _storable_date(value: date) -> date:
    """A date inside the round-trip range of SPEC_DECISIONS 100 (2000-01-01 to 2099-12-31)."""
    if not EARLIEST_DATE <= value <= LATEST_DATE:
        raise ValueError("date")
    return value


type StoredDate = Annotated[date, AfterValidator(_storable_date)]
type Cents = Annotated[int, Field(ge=1, le=BIGINT_MAX)]


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _TitleBody(_Lenient):
    vin: str = Field(pattern=VIN_PATTERN)
    state: str = Field(pattern=r"^[A-Z]{2}$")
    title_status: Literal["CLEAN", "SALVAGE", "JUNK"]
    lienholder_name: str | None = Field(max_length=200)
    lien_recorded_on: StoredDate | None


class _PaymentBody(_Lenient):
    payment_id: CanonicalUuid
    vin: str = Field(pattern=VIN_PATTERN)
    paid_on: StoredDate
    amount_cents: Cents
    deposit_bank_code: str = Field(pattern=r"^[A-Z]{3,12}$")


class _PaymentPageBody(_Lenient):
    items: list[_PaymentBody]
    total: int = Field(ge=0, le=BIGINT_MAX)


class _ContractBody(_Lenient):
    contract_id: CanonicalUuid
    vin: str = Field(pattern=VIN_PATTERN)
    lender_name: str = Field(max_length=200)
    amount_cents: Cents
    signed_on: StoredDate


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
    """The model of a body read within the bounds of every answer (`plain_json`: no key twice, at most 32
    levels deep, no float, no text the bank cannot store), then validated."""
    try:
        plain_json(response)
        return model.model_validate_json(response.content)
    except (ValidationError, ValueError):
        raise OutsideRecordsProtocolError(response.status_code) from None


class _PaymentPages:
    """The pages of one payments window, each checked against decision 52 as it arrives."""

    def __init__(self, vin: Vin, window_from: date, window_to: date) -> None:
        self._vin, self._from, self._to = vin.value, window_from, window_to
        self._items: list[_PaymentBody] = []
        self._total: int | None = None

    @property
    def held(self) -> int:
        """The number of payments held so far: the offset of the next page."""
        return len(self._items)

    @property
    def complete(self) -> bool:
        """True once `total` payments are held."""
        return self._total is not None and len(self._items) == self._total

    def add(self, page: _PaymentPageBody, status: int) -> None:
        """Take one page, or raise MALFORMED_RESPONSE when it breaks the window."""
        if self._total is None:
            self._total = page.total
        newest = self._items[-1].paid_on if self._items else self._to
        held = len(self._items) + len(page.items)
        if (
            page.total != self._total
            or page.total > MAX_PAYMENTS_IN_WINDOW
            or len(page.items) > PAYMENTS_PAGE_LIMIT
            or held > page.total
            or (not page.items and held < page.total)
            or not _newest_first(newest, page.items)
            or any(item.vin != self._vin or not self._from <= item.paid_on <= self._to for item in page.items)
        ):
            raise OutsideRecordsProtocolError(status)
        self._items.extend(page.items)

    def window(self) -> PaymentWindow:
        """The complete window: every payment held, which is `total`."""
        payments = tuple(
            PaymentRecord(item.payment_id, item.paid_on, item.amount_cents, item.deposit_bank_code)
            for item in self._items
        )
        return PaymentWindow(self._from, self._to, payments, len(payments))


def _newest_first(previous: date, items: list[_PaymentBody]) -> bool:
    """True when `paid_on` never increases, starting from the last payment already held."""
    for item in items:
        if item.paid_on > previous:
            return False
        previous = item.paid_on
    return True


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
        """`GET /v1/payments?vin&from&to&limit=200&offset`: every payment in the inclusive window.

        Pages with `offset` until `total` items are held, and proves the window complete
        (SPEC_DECISIONS 52): any page that cannot belong to one consistent, newest first
        answer about this vehicle and window is MALFORMED_RESPONSE.
        """
        pages = _PaymentPages(vin, window_from, window_to)
        while not pages.complete:
            params: dict[str, str | int] = {
                "vin": vin.value,
                "from": window_from.isoformat(),
                "to": window_to.isoformat(),
                "limit": PAYMENTS_PAGE_LIMIT,
                "offset": pages.held,
            }
            response = self._caller.get(PAYMENTS_ROUTE, "/v1/payments", params)
            if response.status_code != httpx.codes.OK:
                raise self._failure(response)
            pages.add(_parse(_PaymentPageBody, response), response.status_code)
        return pages.window()

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
