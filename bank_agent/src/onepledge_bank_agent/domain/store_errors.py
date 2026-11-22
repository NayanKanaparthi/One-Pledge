"""What the bank's own store says when it refuses a write or cannot be reached (SPEC_DECISIONS 85, 92).

Every repository port raises these, whichever implementation is behind it: the PostgreSQL
unit of work maps the database's SQLSTATE to them, the in-memory store raises them
itself. The use cases decide on these classes only, never on a driver's exception.
"""

from __future__ import annotations

from onepledge_bank_agent.domain.errors import DomainError

UNIQUE = "UNIQUE"
"""The refusal code of a write that would duplicate a unique key (SQLSTATE 23505)."""
GUARD_CODES = frozenset({"OP001", "OP002", "OP003", "OP004", "OP005", "OP006"})
"""The SQLSTATEs the bank's own guards raise (SPEC 12.4, SPEC_DECISIONS 46, 47, 87)."""


class StoreRefusedError(DomainError):
    """The store refused a write: a guard (`OP001` to `OP006`), a unique key (`UNIQUE`), or another
    constraint or value rule, named by its SQLSTATE. Repeating the same write is refused again."""

    def __init__(self, refusal: str, sqlstate: str, constraint: str | None = None) -> None:
        super().__init__()
        self.refusal = refusal
        self.sqlstate = sqlstate
        self.constraint = constraint


class StoreUnavailableError(DomainError):
    """The store could not complete the transaction for a reason that may pass: the connection was lost
    or timed out, or the transaction lost a serialisation conflict or a deadlock."""

    def __init__(self, sqlstate: str | None = None) -> None:
        super().__init__()
        self.sqlstate = sqlstate


class ContradictsStoredStateError(DomainError):
    """A registry message describes a state that contradicts what the bank already holds
    (SPEC_DECISIONS 85, rule 3). Nothing is changed; the message is quarantined."""
