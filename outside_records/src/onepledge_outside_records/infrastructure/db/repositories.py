"""SQL implementations of the read port (app role) and the two write ports (owner role)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from sqlalchemy import Connection, Engine, delete, func, select, text
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
from onepledge_outside_records.infrastructure.db.failures import (
    INSUFFICIENT_PRIVILEGE,
    UNDEFINED_TABLE,
    connection_failed,
    privilege_missing,
    sqlstate,
    tables_missing,
)
from onepledge_outside_records.infrastructure.db.models import (
    ApiClientRow,
    ContractRow,
    PaymentRow,
    TitleRecordRow,
)
from onepledge_outside_records.infrastructure.db.role_check import (
    GRANT_MATRIX,
    inspect_role,
    owner_problem,
    refusal,
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
    """One transaction as the owner role, after the owner check; failures become one sentence each."""
    try:
        with engine.begin() as connection:
            problem = owner_problem(connection)
            if problem is not None:
                raise problem
            yield connection
    except OperationalError as error:
        raise connection_failed(error, "MIGRATION_DATABASE_URL") from None
    except ProgrammingError as error:
        if sqlstate(error) == UNDEFINED_TABLE:
            raise tables_missing() from None
        if sqlstate(error) == INSUFFICIENT_PRIVILEGE:
            raise privilege_missing(
                "MIGRATION_DATABASE_URL", "use the owner role created by db provision"
            ) from None
        raise
    except (DBAPIError, psycopg.Error):
        raise OperatorError(
            "The database rejected the records because a constraint failed; nothing was changed.",
            "INVALID_INPUT",
        ) from None


_MISSING_TABLES = text(
    "SELECT count(*) FROM unnest(CAST(:tables AS text[])) AS t(name) "
    "WHERE to_regclass('public.' || t.name) IS NULL"
)


class SqlRecordsReader:
    """Reads records and clients. Built on the app role engine, which can only SELECT."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def ensure_readable(self) -> None:
        """Check before serving that the database is reachable and migrated, or say what to do."""
        try:
            with self._engine.connect() as connection:
                missing: int = connection.execute(
                    _MISSING_TABLES, {"tables": sorted(GRANT_MATRIX)}
                ).scalar_one()
        except OperationalError as error:
            raise connection_failed(error, "DATABASE_URL") from None
        if missing:
            raise tables_missing()

    def ensure_read_only(self) -> None:
        """Refuse to serve as a role that is not least-privilege (SPEC 6.1, SPEC_DECISIONS 72).

        `inspect_role` compares the role with the grant matrix on all six points, so "cannot
        write even if the code tried" is a property checked at startup, not only of the
        deployment.
        """
        with _connection(self._engine) as connection:
            violations = inspect_role(connection)
        if violations:
            raise refusal(violations)

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

    def active_client(self, api_key_sha256: str) -> ApiClient | None:
        """The active client with this key hash."""
        statement = select(ApiClientRow).where(
            ApiClientRow.api_key_sha256 == api_key_sha256, ApiClientRow.active
        )
        with _connection(self._engine) as connection:
            row = connection.execute(statement).first()
        if row is None:
            return None
        return ApiClient(str(row.client_id), row.name)


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
                raise OperatorError(
                    "The owner connection does not use the psycopg driver.", "CONFIGURATION_ERROR"
                )
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
