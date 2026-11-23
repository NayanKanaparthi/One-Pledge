"""Reading rows that may not read back (SPEC_DECISIONS 100, third layer).

The check constraints of migration 0012 keep values another party supplied inside the range
every reader holds, but a superuser can drop a constraint and force a row in. `read_rows`
reads a statement's rows and turns each into a domain record; when the driver cannot decode a
value or a record refuses one, it finds the rows that cannot be read, by primary key, and
raises `StoreUnreadableError` naming the table and those keys, so the reader reports them
instead of failing as a whole.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy import Select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import InstrumentedAttribute, Session

from onepledge_bank_agent.domain.errors import DomainError
from onepledge_bank_agent.domain.store_errors import StoreUnreadableError

MAX_NAMED_ROWS = 200
UNREADABLE = (DataError, DomainError, ValueError, OverflowError)
"""What reading or converting one stored row can raise when a value is outside what it holds."""


def read_rows[R, T](
    session: Session,
    statement: Select[R],
    key: InstrumentedAttribute[object],
    convert: Callable[[R], T],
    table: str,
) -> list[T]:
    """Every row of `statement` as a record, or `StoreUnreadableError` naming the rows that cannot be read."""
    try:
        return [convert(row) for row in session.execute(statement).scalars()]
    except UNREADABLE:
        session.expunge_all()
    raise StoreUnreadableError(table, _unreadable_keys(session, statement, key, convert))


def _unreadable_keys[R, T](
    session: Session,
    statement: Select[R],
    key: InstrumentedAttribute[object],
    convert: Callable[[R], T],
) -> tuple[str, ...]:
    keys: Sequence[object] = session.execute(statement.with_only_columns(key)).scalars().all()
    found: list[str] = []
    for value in keys:
        try:
            convert(session.execute(statement.where(key == value)).scalars().one())
        except UNREADABLE:
            found.append(str(value))
            session.expunge_all()
            if len(found) == MAX_NAMED_ROWS:
                break
    return tuple(found)
