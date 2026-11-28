"""Creating and dropping one holder's database and its two roles with the superuser URL.

The shape is `outside_records`' (SPEC section 2): `{db}_owner` owns the database and every
object, `{db}_app` may only connect and use the schema, and gets its table privileges in a
migration. Provisioning is idempotent: existing roles get their passwords reset, an
existing database is kept. Each holder has its own database (SPEC 4.1).
"""

from __future__ import annotations

import re

import psycopg
from psycopg import sql
from psycopg.rows import TupleRow

from onepledge_key_holder.infrastructure.db.engines import DatabaseTarget, OperatorError

_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _check_names(target: DatabaseTarget) -> None:
    for name in (target.database, target.owner_role, target.app_role):
        if not _NAME.fullmatch(name):
            raise OperatorError("Database and role names must be lower case letters, digits and underscores.")


class DatabaseProvisioner:
    """Runs provisioning statements as the PostgreSQL superuser."""

    def __init__(self, admin_url: str) -> None:
        self._admin_url = admin_url

    def _connect(self, database: str | None = None) -> psycopg.Connection[TupleRow]:
        try:
            if database is None:
                return psycopg.connect(self._admin_url, autocommit=True)
            return psycopg.connect(self._admin_url, autocommit=True, dbname=database)
        except psycopg.OperationalError:
            raise OperatorError(
                "PostgreSQL is not reachable at PG_ADMIN_URL; start it or fix the URL."
            ) from None

    def provision(self, target: DatabaseTarget) -> None:
        """Create or update both roles, create the database, and set its privileges."""
        _check_names(target)
        with self._connect() as connection:
            row = connection.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
            if row is None or row[0] is not True:
                raise OperatorError(
                    "PG_ADMIN_URL connects as a role that is not a superuser; db provision needs one."
                )
            for role, password in (
                (target.owner_role, target.owner_password),
                (target.app_role, target.app_password),
            ):
                self._ensure_role(connection, role, password)
            exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (target.database,)
            ).fetchone()
            if exists is None:
                connection.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(
                        sql.Identifier(target.database), sql.Identifier(target.owner_role)
                    )
                )
            database = sql.Identifier(target.database)
            connection.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database))
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    database, sql.Identifier(target.app_role)
                )
            )
        with self._connect(target.database) as connection:
            connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(target.app_role))
            )

    @staticmethod
    def _ensure_role(connection: psycopg.Connection[TupleRow], role: str, password: str) -> None:
        exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        statement = sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(password)
        )
        connection.execute(statement)

    def drop(self, target: DatabaseTarget) -> None:
        """Drop the database and both roles (used by tests and resets)."""
        _check_names(target)
        with self._connect() as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(target.database))
            )
            for role in (target.app_role, target.owner_role):
                if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                    connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                    connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
