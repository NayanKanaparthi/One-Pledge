"""The FastAPI application of outside records (`contracts/outside_records.yaml`).

Routes validate, authenticate, call one use case and map the result. Every lookup
needs an `X-API-Key`; `/healthz` does not.
"""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from onepledge_outside_records.application.lookups import (
    AuthenticateClient,
    GetContracts,
    GetTitle,
    ListPayments,
)
from onepledge_outside_records.application.ports import RecordsReader
from onepledge_outside_records.domain.errors import ErrorDetail, ValidationFailedError
from onepledge_outside_records.domain.records import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_LIMIT,
    Contract,
    Payment,
    PaymentQuery,
    TitleRecord,
    VinText,
)
from onepledge_outside_records.interface.http.plumbing import AccessLogMiddleware, install_error_handlers


def _title_json(record: TitleRecord) -> dict[str, object]:
    return {
        "vin": record.vin,
        "state": record.state,
        "title_status": record.title_status,
        "lienholder_name": record.lienholder_name,
        "lien_recorded_on": record.lien_recorded_on.isoformat() if record.lien_recorded_on else None,
    }


def _payment_json(payment: Payment) -> dict[str, object]:
    return {
        "payment_id": payment.payment_id,
        "vin": payment.vin,
        "paid_on": payment.paid_on.isoformat(),
        "amount_cents": payment.amount_cents,
        "deposit_bank_code": payment.deposit_bank_code,
    }


def _contract_json(contract: Contract) -> dict[str, object]:
    return {
        "contract_id": contract.contract_id,
        "vin": contract.vin,
        "lender_name": contract.lender_name,
        "amount_cents": contract.amount_cents,
        "signed_on": contract.signed_on.isoformat(),
        "document_sha256": contract.document_sha256,
    }


def _date(value: str | None, name: str, details: list[ErrorDetail]) -> date | None:
    if value is None:
        return None
    if len(value) == len("YYYY-MM-DD"):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    details.append(ErrorDetail(name, "INVALID_DATE"))
    return None


def _integer(
    value: str | None, name: str, default: int, bounds: tuple[int, int | None], details: list[ErrorDetail]
) -> int:
    if value is None:
        return default
    if not value.isdigit():
        details.append(ErrorDetail(name, "INVALID_INTEGER"))
        return default
    number = int(value)
    low, high = bounds
    if number < low or (high is not None and number > high):
        details.append(ErrorDetail(name, "OUT_OF_RANGE"))
    return number


def payment_query(request: Request) -> PaymentQuery:
    """Validate the query string of `GET /v1/payments`; details name fields and issues only."""
    params = request.query_params
    details: list[ErrorDetail] = []
    vin_text = params.get("vin")
    vin: VinText | None = None
    if vin_text is None:
        details.append(ErrorDetail("vin", "MISSING"))
    else:
        try:
            vin = VinText.parse(vin_text)
        except ValidationFailedError as error:
            details.extend(error.details)
    paid_from = _date(params.get("from"), "from", details)
    paid_to = _date(params.get("to"), "to", details)
    limit = _integer(params.get("limit"), "limit", DEFAULT_LIMIT, (MIN_LIMIT, MAX_LIMIT), details)
    offset = _integer(params.get("offset"), "offset", 0, (0, None), details)
    if details or vin is None:
        raise ValidationFailedError(tuple(details))
    return PaymentQuery(vin, paid_from, paid_to, limit, offset)


def create_app(reader: RecordsReader) -> FastAPI:
    """The app around one records reader."""
    authenticate = AuthenticateClient(reader)
    get_title, list_payments, get_contracts = GetTitle(reader), ListPayments(reader), GetContracts(reader)
    app = FastAPI(title="OnePledge outside records", docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handlers(app)
    app.add_middleware(AccessLogMiddleware)

    def client(request: Request) -> None:
        found = authenticate.run(request.headers.get("x-api-key"))
        request.scope["onepledge.client_id"] = found.client_id

    @app.get("/v1/titles/{vin}")
    def title(vin: str, request: Request) -> JSONResponse:
        client(request)
        return JSONResponse(_title_json(get_title.run(VinText.parse(vin))))

    @app.get("/v1/payments")
    def payments(request: Request) -> JSONResponse:
        client(request)
        page = list_payments.run(payment_query(request))
        body = {
            "items": [_payment_json(p) for p in page.items],
            "total": page.total,
            "limit": page.limit,
            "offset": page.offset,
        }
        return JSONResponse(body)

    @app.get("/v1/contracts/{vin}")
    def contracts(vin: str, request: Request) -> JSONResponse:
        client(request)
        found = get_contracts.run(VinText.parse(vin))
        return JSONResponse({"vin": found[0].vin, "contracts": [_contract_json(c) for c in found]})

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "outside_records"})

    return app
