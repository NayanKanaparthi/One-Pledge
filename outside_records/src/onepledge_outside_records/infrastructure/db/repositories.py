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
            "PostgreSQL refused the owner connection at MIGRATION_DATABASE_URL; run db provision first.",
            "DATABASE_UNAVAILABLE",
        ) from None
    except ProgrammingError:
        raise OperatorError(
            "The outside records tables are missing; run db migrate first.", "NOT_MIGRATED"
        ) from None
    except (DBAPIError, psycopg.Error):
        raise OperatorError(
            "The database rejected the records (a constraint failed); nothing was changed.", "INVALID_INPUT"
        ) from None


# What the connected role is: superuser, owner of a table of the schema, holder of a write privilege.
_ROLE_PRIVILEGES = """
SELECT
    (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),
    EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = current_schema() AND tableowner = current_user),
    EXISTS (
        SELECT 1 FROM pg_tables AS t, unnest(ARRAY['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) AS p(privilege)
        WHERE t.schemaname = current_schema()
          AND has_table_privilege(current_user, quote_ident(t.schemaname) || '.' || quote_ident(t.tablename),
                                  p.privilege)
    )
"""


def _unsafe_role(problem: str) -> OperatorError:
    return OperatorError(
        f"The role of DATABASE_URL {problem}; serve must connect as the read-only app role.",
        "UNSAFE_DATABASE_ROLE",
    )


class SqlRecordsReader:
    """Reads records and clients. Built on the app role engine, which can only SELECT."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def ensure_readable(self) -> None:
        """Check before serving that the role can read the migrated tables, or say what to run."""
        try:
            with self._engine.connect() as connection:
                connection.execute(select(ApiClientRow.client_id).limit(1))
        except DBAPIError:
            raise OperatorError(
                "The database at DATABASE_URL cannot be read; run db provision and db migrate first.",
                "NOT_MIGRATED",
            ) from None

    def ensure_read_only(self) -> None:
        """Refuse to serve as a role that could write (SPEC 6.1, SPEC_DECISIONS 43.4).

        The serving role must not be a superuser, must own no table of the schema, and must
        hold none of INSERT, UPDATE, DELETE or TRUNCATE on any table, so "cannot write even if
        the code tried" is a property checked at startup, not only of the deployment.
        """
        with _connection(self._engine) as connection:
            superuser, owner, writer = connection.execute(text(_ROLE_PRIVILEGES)).one()
        if superuser:
            raise _unsafe_role("is a superuser")
        if owner:
            raise _unsafe_role("owns tables of the outside records schema")
        if writer:
            raise _unsafe_role("may insert, update, delete or truncate rows")

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
