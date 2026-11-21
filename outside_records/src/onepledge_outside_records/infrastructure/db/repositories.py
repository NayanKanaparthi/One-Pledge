"""SQL implementations of the read port (app role) and the two write ports (owner role)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from sqlalchemy import Connection, Engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from onepledge_outside_records.application.ports import RecordSet
from onepledge_outside_records.domain.errors import OperatorError, ServiceUnavailableError
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
from onepledge_outside_records.infrastructure.db.models import (
    ApiClientRow,
    ContractRow,
    PaymentRow,
    TitleRecordRow,
)

COPY_TITLES = "COPY title_records (vin, state, title_status, lienholder_name, lien_recorded_on) FROM STDIN"
COPY_PAYMENTS = "COPY payments (payment_id, vin, paid_on, amount_cents, deposit_bank_code) FROM STDIN"
COPY_CONTRACTS = (
    "COPY contracts (contract_id, vin, lender_name, amount_cents, signed_on, document_sha256) FROM STDIN"
)


@contextmanager
def _connection(engine: Engine) -> Iterator[Connection]:
    try:
        with engine.connect() as connection:
            yield connection
    except OperationalError:
        raise ServiceUnavailableError from None


@contextmanager
def _owner_transaction(engine: Engine) -> Iterator[Connection]:
    """One transaction as the owner role; database errors become one sentence, without values."""
    try:
        with engine.begin() as connection:
            yield connection
    except OperationalError:
        raise OperatorError(
            "PostgreSQL refused the owner connection at MIGRATION_DATABASE_URL; run db provision first."
        ) from None
    except ProgrammingError:
        raise OperatorError("The outside records tables are missing; run db migrate first.") from None
    except (DBAPIError, psycopg.Error):
        raise OperatorError(
            "The database rejected the records (a constraint failed); nothing was changed."
        ) from None


class SqlRecordsReader:
    """Reads records and clients. Built on the app role engine, which can only SELECT."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def title(self, vin: VinText) -> TitleRecord | None:
        """The title record of a VIN."""
        with _connection(self._engine) as connection:
            row = connection.execute(select(TitleRecordRow).where(TitleRecordRow.vin == vin.value)).first()
        if row is None:
            return None
        return TitleRecord(row.vin, row.state, row.title_status, row.lienholder_name, row.lien_recorded_on)

    def payments(self, query: PaymentQuery) -> PaymentPage:
        """Payments of the VIN inside the optional window, newest first."""
        conditions = [PaymentRow.vin == query.vin.value]
        if query.paid_from is not None:
            conditions.append(PaymentRow.paid_on >= query.paid_from)
        if query.paid_to is not None:
            conditions.append(PaymentRow.paid_on <= query.paid_to)
        page = (
            select(PaymentRow)
            .where(*conditions)
            .order_by(PaymentRow.paid_on.desc(), PaymentRow.payment_id)
            .limit(query.limit)
            .offset(query.offset)
        )
        with _connection(self._engine) as connection:
            total = connection.execute(
                select(func.count()).select_from(PaymentRow).where(*conditions)
            ).scalar_one()
            rows = connection.execute(page).all()
        items = tuple(
            Payment(str(r.payment_id), r.vin, r.paid_on, r.amount_cents, r.deposit_bank_code) for r in rows
        )
        return PaymentPage(items, total, query.limit, query.offset)

    def contracts(self, vin: VinText) -> tuple[Contract, ...]:
        """Contracts of a VIN, oldest signature first."""
        statement = (
            select(ContractRow)
            .where(ContractRow.vin == vin.value)
            .order_by(ContractRow.signed_on, ContractRow.contract_id)
        )
        with _connection(self._engine) as connection:
            rows = connection.execute(statement).all()
        return tuple(
            Contract(str(r.contract_id), r.vin, r.lender_name, r.amount_cents, r.signed_on, r.document_sha256)
            for r in rows
        )

    def active_client(self, api_key_sha256: str) -> tuple[ApiClient, str] | None:
        """The active client with this key hash."""
        statement = select(ApiClientRow).where(
            ApiClientRow.api_key_sha256 == api_key_sha256, ApiClientRow.active
        )
        with _connection(self._engine) as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return ApiClient(str(row.client_id), row.name), row.api_key_sha256


class SqlRecordsWriter:
    """Replaces the three data tables in one transaction with COPY (owner role)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def replace_all(self, records: RecordSet) -> RecordCounts:
        """Delete and reload; the same input always gives the same contents."""
        with _owner_transaction(self._engine) as connection:
            for model in (TitleRecordRow, PaymentRow, ContractRow):
                connection.execute(delete(model))
            raw = connection.connection.driver_connection
            if not isinstance(raw, psycopg.Connection):
                raise OperatorError("The owner connection does not use the psycopg driver.")
            with raw.cursor() as cursor:
                with cursor.copy(COPY_TITLES) as copy:
                    for t in records.titles:
                        copy.write_row(
                            (t.vin, t.state, t.title_status, t.lienholder_name, t.lien_recorded_on)
                        )
                with cursor.copy(COPY_PAYMENTS) as copy:
                    for p in records.payments:
                        copy.write_row((p.payment_id, p.vin, p.paid_on, p.amount_cents, p.deposit_bank_code))
                with cursor.copy(COPY_CONTRACTS) as copy:
                    for c in records.contracts:
                        copy.write_row(
                            (
                                c.contract_id,
                                c.vin,
                                c.lender_name,
                                c.amount_cents,
                                c.signed_on,
                                c.document_sha256,
                            )
                        )
            counts = [
                connection.execute(select(func.count()).select_from(model)).scalar_one()
                for model in (TitleRecordRow, PaymentRow, ContractRow)
            ]
        return RecordCounts(*counts)


class SqlClientRegistry:
    """Upserts API clients by name (owner role)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, name: str, api_key_sha256: str) -> ApiClient:
        """Insert, or replace the key and reactivate."""
        values = insert(ApiClientRow).values(
            client_id=uuid.uuid4(), name=name, api_key_sha256=api_key_sha256, active=True
        )
        statement = values.on_conflict_do_update(
            index_elements=[ApiClientRow.name],
            set_={"api_key_sha256": values.excluded.api_key_sha256, "active": True},
        ).returning(ApiClientRow.client_id)
        with _owner_transaction(self._engine) as connection:
            client_id: uuid.UUID = connection.execute(statement).scalar_one()
        return ApiClient(str(client_id), name)
