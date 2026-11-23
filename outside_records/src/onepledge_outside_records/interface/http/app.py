"""The FastAPI application of outside records (`contracts/outside_records.yaml`).

Routes validate, authenticate, call one use case and map the result. Every lookup
needs an `X-API-Key`; `/healthz` does not.
"""

from __future__ import annotations

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
from onepledge_outside_records.interface.http.plumbing import header, new_service_app, refuse_repeated_headers
from onepledge_outside_records.interface.http.query import MAX_OFFSET, QueryParameters


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


PAYMENT_PARAMETERS = ("vin", "from", "to", "limit", "offset")


def payment_query(request: Request) -> PaymentQuery:
    """Validate the query string of `GET /v1/payments`; details name fields and issues only."""
    query = QueryParameters(request.query_params.multi_items(), PAYMENT_PARAMETERS)
    vin_text = query.text("vin")
    vin: VinText | None = None
    if vin_text is None:
        query.add("vin", "REQUIRED")
    else:
        try:
            vin = VinText.parse(vin_text)
        except ValidationFailedError as error:
            for detail in error.details:
                query.add(detail.field, detail.issue)
    paid_from, paid_to = query.date("from"), query.date("to")
    limit = query.integer("limit", DEFAULT_LIMIT, MIN_LIMIT, MAX_LIMIT)
    offset = query.integer("offset", 0, 0, MAX_OFFSET)
    query.finish()
    if vin is None:
        raise ValidationFailedError((ErrorDetail("vin", "REQUIRED"),))
    return PaymentQuery(vin, paid_from, paid_to, limit, offset)


def no_parameters(request: Request) -> None:
    """A route that declares no query parameter refuses any."""
    QueryParameters(request.query_params.multi_items(), ()).finish()


def create_app(reader: RecordsReader) -> FastAPI:
    """The app around one records reader."""
    authenticate = AuthenticateClient(reader)
    get_title, list_payments, get_contracts = GetTitle(reader), ListPayments(reader), GetContracts(reader)
    app = new_service_app("OnePledge outside records")

    def client(request: Request) -> None:
        found = authenticate.run(header(request, "X-API-Key"))
        request.scope["onepledge.client_name"] = found.name
        refuse_repeated_headers(request)

    @app.get("/v1/titles/{vin}")
    def title(vin: str, request: Request) -> JSONResponse:
        client(request)
        no_parameters(request)
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
        no_parameters(request)
        found = get_contracts.run(VinText.parse(vin))
        return JSONResponse({"vin": found[0].vin, "contracts": [_contract_json(c) for c in found]})

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        # Probes add cache busters: health ignores its query (SPEC_DECISIONS 41).
        return JSONResponse({"status": "ok", "service": "outside_records"})

    return app
