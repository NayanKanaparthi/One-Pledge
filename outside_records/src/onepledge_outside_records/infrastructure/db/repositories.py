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
from onepledge_outside_records.infrastructure.db.session import (
    refuse_replica_sessions,
    session_parameter_problems,
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


_ENCODING = text(
    "SELECT current_setting('server_encoding') COLLATE \"C\" = 'UTF8', current_setting('server_encoding')"
)
_REVISION_READABLE = text("SELECT has_table_privilege(current_user, 'public.alembic_version', 'SELECT')")
_REVISION = text("SELECT (SELECT version_num FROM alembic_version LIMIT 1), current_database()")


class SqlRecordsReader:
    """Reads records and clients. Built on the app role engine, which can only SELECT."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # The startup checks of `serve` (SPEC_DECISIONS 104). A connection that does not open is the
    # reachability check's own sentence; any statement the database refuses after that reaches
    # the check's boundary, which names the check and the SQLSTATE.

    def ensure_reachable(self) -> None:
        """The database of DATABASE_URL accepts a connection and answers one statement."""
        connection = self._open()
        with connection:
            connection.execute(text("SELECT 1"))

    def ensure_utf8(self) -> None:
        """The database encoding is UTF8, the one under which "C" orders by code point (SPEC_DECISIONS
        133.1)."""
        with self._open() as connection:
            utf8, shown = connection.execute(_ENCODING).one()
        if not utf8:
            name = shown.decode("ascii", "replace") if isinstance(shown, bytes) else str(shown)
            raise OperatorError(
                f"The database of DATABASE_URL has the encoding {name}, not UTF8; create it again with "
                "db provision, which makes it UTF8.",
                "CONFIGURATION_ERROR",
            )

    def ensure_session_parameters(self) -> None:
        """Every session parameter the service sets holds in the session (SPEC_DECISIONS 103.7)."""
        with self._open() as connection:
            differing = session_parameter_problems(connection)
        if differing:
            raise OperatorError(
                "The database did not keep the session parameters the service sets "
                f"({', '.join(differing)}); check the database's configuration.",
                "CONFIGURATION_ERROR",
            )

    def ensure_at_head(self, head: str) -> None:
        """The schema is in place at the head revision of the installed migrations (SPEC 12.5,
        SPEC_DECISIONS 128 and 132): tables missing, or another revision, each has its sentence.

        Without SELECT on `alembic_version` the revision is not read here: the role inspection
        that follows names the missing privilege, which `db migrate` restores.
        """
        with self._open() as connection:
            missing: int = connection.execute(_MISSING_TABLES, {"tables": sorted(GRANT_MATRIX)}).scalar_one()
            if missing:
                raise tables_missing()
            readable: bool = connection.execute(_REVISION_READABLE).scalar_one()
            if not readable:
                return
            current, database = connection.execute(_REVISION).one()
        if current is None:
            raise tables_missing()
        if current != head:
            raise OperatorError(
                f"The database {database} is at revision {current}, not at {head} as this program "
                "expects; run db migrate with this program, or run the program that matches the database.",
                "NOT_MIGRATED",
            )

    def ensure_read_only(self) -> None:
        """Refuse to serve as a role that is not least-privilege (SPEC 6.1, SPEC_DECISIONS 72, 103).

        `inspect_role` compares the role with the grant matrix on all ten points, so "cannot
        write even if the code tried" is a property checked at startup, not only of the
        deployment.
        """
        with self._open() as connection:
            violations = inspect_role(connection)
        if violations:
            raise refusal(violations)

    def guard_new_connections(self) -> None:
        """From now on, refuse every new pooled connection whose session would not fire triggers."""
        self._engine.dispose()
        refuse_replica_sessions(self._engine)

    def _open(self) -> Connection:
        try:
            return self._engine.connect()
        except OperationalError as error:
            raise connection_failed(error, "DATABASE_URL") from None

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
        taken = select(ApiClientRow.name).where(
            ApiClientRow.api_key_sha256 == api_key_sha256, ApiClientRow.name != name
        )
        with _owner_transaction(self._engine) as connection:
            # Locks out a concurrent registration of the same key until this transaction ends.
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": api_key_sha256})
            if connection.execute(taken).first() is not None:
                raise OperatorError(
                    "Another client already has this key; generate a new key for this client.",
                    "INVALID_INPUT",
                )
            client_id: uuid.UUID = connection.execute(statement).scalar_one()
        return ApiClient(str(client_id), name)
