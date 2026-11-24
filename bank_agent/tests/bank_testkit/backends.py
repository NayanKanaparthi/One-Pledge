"""The two implementations of the repository ports, behind one shape, for the shared port tests.

`memory_backend` is the in-memory store of `bank_testkit.memory`; `postgres_backend` is a
fresh migrated bank database reached as the application role (and as the owner role for
reference data, which only seeding writes). A `PortWorld` seeds one lender and two
facilities through the ports themselves and builds the parent rows a test needs.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime

from bank_testkit.database import (
    drop,
    insert_user,
    new_bank_database,
    provision_and_migrate,
    provision_and_migrate_icu,
)
from bank_testkit.memory.clocks import ManualClock
from bank_testkit.memory.stores import MemoryStore, MemoryUnits
from bank_testkit.records import T0, lei, loan
from bank_testkit.tapes import SUMMIT_LAYOUT
from onepledge_bank_agent.application.ports import UnitOfWorkFactory
from onepledge_bank_agent.domain.audit import Actor, AuditDraft
from onepledge_bank_agent.domain.canonical_json import JsonValue
from onepledge_bank_agent.domain.entities import Facility, Lender
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    AuditObjectType,
    FacilityStatus,
    Role,
    TapeKind,
    TapeSource,
)
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.tape import TapeFacts, TapeSubmission
from onepledge_bank_agent.domain.values import Lei
from onepledge_bank_agent.infrastructure.audit import HashChainedAuditWriter
from onepledge_bank_agent.infrastructure.db.engines import make_engine
from onepledge_bank_agent.infrastructure.db.session import SqlUnitOfWorkFactory
from onepledge_bank_agent.infrastructure.ids import RandomIds


@dataclass(frozen=True)
class PortBackend:
    """One implementation: units as the application role, units as the owner role, and a user maker."""

    name: str
    units: UnitOfWorkFactory
    owner_units: UnitOfWorkFactory
    new_user: Callable[[], uuid.UUID]


def memory_backend() -> PortBackend:
    """The in-memory store; a user is kept as PostgreSQL keeps it (`insert_user`): an active analyst
    with its `USER_CREATED` entry."""
    store = MemoryStore()
    units = MemoryUnits(store)

    def new_user() -> uuid.UUID:
        user_id = uuid.uuid4()
        detail: dict[str, JsonValue] = {"role": Role.ANALYST.value, "active": True}
        with units() as unit:
            unit.record(AuditDraft(AuditAction.USER_CREATED, Actor.system(), AuditObjectType.USER, user_id,
                                   detail=detail))  # fmt: skip
            unit.commit()
        store.state.users[user_id] = (Role.ANALYST.value, True)  # after the commit, which replaces the state
        return user_id

    return PortBackend("memory", units, units, new_user)


@contextmanager
def postgres_backend(*, icu: bool = False) -> Iterator[PortBackend]:
    """A provisioned and migrated bank database, dropped on exit; with `icu` its default collation is
    ICU's `und` (SPEC_DECISIONS 133)."""
    database = new_bank_database()
    (provision_and_migrate_icu if icu else provision_and_migrate)(database)
    app, owner = make_engine(database.app_url), make_engine(database.owner_url)
    clock = ManualClock()
    try:
        yield PortBackend(
            "postgres-icu" if icu else "postgres",
            SqlUnitOfWorkFactory(app, HashChainedAuditWriter(clock, RandomIds()), RandomIds()),
            SqlUnitOfWorkFactory(owner, HashChainedAuditWriter(clock, RandomIds()), RandomIds()),
            lambda: insert_user(database, Role.ANALYST),
        )
    finally:
        app.dispose()
        owner.dispose()
        drop(database)


class PortWorld:
    """A backend with one lender, two facilities and one user, and builders of committed parent rows."""

    def __init__(self, backend: PortBackend) -> None:
        self.backend = backend
        self.units = backend.units
        self.user = backend.new_user()
        self.lender = Lender(uuid.uuid4(), "Summit Auto Finance LLC", Lei(lei("00000ONEPLEDGESUM1")))
        self.facility = self.new_facility("HARBOR-SUMMIT")
        self.other_facility = self.new_facility("HARBOR-OTHER")
        with backend.owner_units() as unit:
            unit.facilities.upsert_lender(self.lender)
            unit.facilities.upsert_facility(self.facility)
            unit.facilities.upsert_facility(self.other_facility)
            unit.commit()

    def new_facility(self, code: str) -> Facility:
        """An ACTIVE facility of the seeded lender, not stored."""
        return Facility(
            uuid.uuid4(), self.lender.lender_id, code, f"{code} line", 7_500_000_000, 8500,
            FacilityStatus.ACTIVE, date(2025, 3, 1),
        )  # fmt: skip

    def tape_of(
        self,
        uploaded: datetime = T0,
        *,
        facility_id: uuid.UUID | None = None,
        kind: TapeKind = TapeKind.PLEDGE,
        file_sha256: str | None = None,
    ) -> TapeSubmission:
        """A RECEIVED submission, not stored."""
        facts = TapeFacts(
            uuid.uuid4(), facility_id or self.facility.facility_id, kind, TapeSource.FILE_UPLOAD, "t.csv",
            file_sha256 or uuid.uuid4().hex * 2, "b" * 64, date(2026, 10, 15), 1, self.user, uploaded,
        )  # fmt: skip
        return TapeSubmission.receive(facts)

    def tape(
        self,
        uploaded: datetime = T0,
        *,
        facility_id: uuid.UUID | None = None,
        kind: TapeKind = TapeKind.PLEDGE,
    ) -> TapeSubmission:
        """A RECEIVED submission, committed."""
        made = self.tape_of(uploaded, facility_id=facility_id, kind=kind)
        with self.units() as unit:
            unit.tapes.add(made, SUMMIT_LAYOUT)
            unit.commit()
        return made

    def loans(
        self, submission_id: uuid.UUID, vins: list[str], facility_id: uuid.UUID | None = None
    ) -> list[Loan]:
        """PENDING loans first seen on the submission, committed."""
        made = [loan(facility_id or self.facility.facility_id, submission_id, vin) for vin in vins]
        with self.units() as unit:
            unit.loans.add_many(made)
            unit.commit()
        return made
