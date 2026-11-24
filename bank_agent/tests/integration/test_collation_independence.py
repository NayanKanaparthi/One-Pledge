"""The bank agent answers the same on a database whose default collation is not "C" (SPEC_DECISIONS 133).

The server has ICU, so the scratch database is created with the ICU default collation `und`
(`und-x-icu`), which orders text unlike code point: case first-level equal, punctuation before
digits and letters. On it:

- every text column is `"C"`, and ordering by one gives Python's code point order for values
  that ICU orders otherwise (case, digits, punctuation, an accent, a character above U+FFFF);
- unique keys stay case-sensitive;
- one submission written with the same ids into the in-memory store, a "C" database and the ICU
  database reads back the same stage rows in the same order, with the same digests (the parity
  cases of decision 127 also run on this database: the `ports` fixture's `postgres-icu`);
- a Harbor run of the small data set through every stage verifies OK in deep verification.

A case-insensitive, non-deterministic ICU collation cannot be a database default; where it can be
applied (a superuser moves a column onto it) the manifest control names the column
(`test_text_collation.py`).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from bank_testkit.backends import PortBackend, memory_backend, postgres_backend
from bank_testkit.banks import manifest_steps
from bank_testkit.data import seed_reference
from bank_testkit.database import (
    ICU_DEFAULT,
    BankDatabase,
    drop,
    new_bank_database,
    provision_and_migrate_icu,
)
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.memory.bank import vins
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from bank_testkit.records import KEY_V1, check_results, hex64, lei, raw_rows, tokens
from bank_testkit.tapes import SUMMIT_HEADER, SUMMIT_LAYOUT
from onepledge_bank_agent.application.evidence_ports import PlannedItem
from onepledge_bank_agent.application.ports import NewItem
from onepledge_bank_agent.domain.entities import Facility, Lender, RegistryRequest
from onepledge_bank_agent.domain.enums import (
    CleaningAction,
    CleaningProblem,
    FacilityStatus,
    IntegrityRunStatus,
    RegistryItemResult,
    RequestOrigin,
    RowDisposition,
    TapeKind,
    TapeSource,
)
from onepledge_bank_agent.domain.loan import Loan, LoanDraft
from onepledge_bank_agent.domain.registry_records import ItemOutcome, RegistryAnswer
from onepledge_bank_agent.domain.registry_results import StoredResult
from onepledge_bank_agent.domain.stage_digests import DigestRow, StagePart, rows_sha256
from onepledge_bank_agent.domain.tape import TapeFacts, TapeSubmission
from onepledge_bank_agent.domain.values import (
    BorrowerRef,
    CheckResult,
    CleaningIssue,
    Lei,
    LenderLoanNumber,
    LoanTerms,
    Money,
)
from onepledge_bank_agent.domain.vin import Vin

pytestmark = pytest.mark.integration

TRICKY = ["Zeta", "alpha", "_x", "9", "Éclair", "eclair", "e-mail", "\U0001f600", "￯", "B", "b"]
"""Values ICU's `und` and code point order differently."""


@pytest.fixture
def icu_db() -> Iterator[BankDatabase]:
    """A provisioned and migrated bank database whose default collation is ICU's `und`."""
    database = new_bank_database()
    provision_and_migrate_icu(database)
    yield database
    drop(database)


def test_the_icu_database_is_really_not_c(icu_db: BankDatabase) -> None:
    with icu_db.connect("admin") as connection:
        row = connection.execute(
            "SELECT datlocprovider, daticulocale FROM pg_database WHERE datname = current_database()"
        ).fetchone()
        icu = [
            r[0] for r in connection.execute("SELECT v FROM unnest(%s::text[]) AS v ORDER BY v", (TRICKY,))
        ]
    assert row == ("i", ICU_DEFAULT)
    assert icu != sorted(TRICKY), "the default collation really orders these values unlike code point"


def test_a_text_column_orders_by_code_point_on_an_icu_database(icu_db: BankDatabase) -> None:
    with icu_db.connect("admin") as connection:
        for index, name in enumerate(TRICKY):
            connection.execute(
                "INSERT INTO users (user_id, email, display_name, role, password_hash, active, created_at, "
                "updated_at) VALUES (%s, %s, %s, 'ANALYST', 'x', true, now(), now())",
                (uuid.uuid4(), f"user{index}@harbor.example", name),
            )
        found = [r[0] for r in connection.execute("SELECT display_name FROM users ORDER BY display_name")]
        largest = connection.execute("SELECT max(display_name) FROM users").fetchone()
    assert found == sorted(TRICKY)
    assert largest == (max(TRICKY),)


def test_unique_keys_stay_case_sensitive_on_an_icu_database(icu_db: BankDatabase) -> None:
    """Lender names differing only in case are two lenders; the same name twice is refused."""
    names = ("Summit Auto Finance", "summit auto finance", "SUMMIT AUTO FINANCE", "Summit Auto Finance\u0301")
    with icu_db.connect("admin") as connection:
        for index, name in enumerate(names):
            connection.execute(
                "INSERT INTO lenders (lender_id, legal_name, lei, created_at) VALUES (%s, %s, %s, now())",
                (uuid.uuid4(), name, lei(f"00000CASECHECK{index:04d}")),
            )
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                "INSERT INTO lenders (lender_id, legal_name, lei, created_at) VALUES (%s, %s, %s, now())",
                (uuid.uuid4(), names[0], lei("00000CASECHECK0099")),
            )


T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
SUBMISSION = uuid.UUID(int=0x5B)
LENDER = Lender(uuid.UUID(int=0x1E), "Summit Auto Finance LLC", Lei(lei("00000ONEPLEDGESUM1")))
FACILITY = Facility(
    uuid.UUID(int=0xFA), LENDER.lender_id, "HARBOR-SUMMIT", "Summit line", 7_500_000_000, 8500,
    FacilityStatus.ACTIVE, date(2025, 3, 1),
)  # fmt: skip
LOAN_IDS = [uuid.UUID(int=0x10 + n) for n in range(3)]
VINS = vins(3, start=70)
REQUESTS = [
    RegistryRequest(uuid.UUID(int=0x30 + n), SUBMISSION, RequestOrigin.TAPE, n, hex64(("key", n)),
                    TapeKind.PLEDGE, KEY_V1, 1, 0, None, None)
    for n in range(2)
]  # fmt: skip
ITEM_REFS = [uuid.UUID(int=0x40 + n) for n in range(2)]
ISSUES = (
    CleaningIssue("vin", CleaningProblem.WHITESPACE_TRIMMED, CleaningAction.FIXED),
    CleaningIssue("borrower_ref", CleaningProblem.BORROWER_REF_NORMALISED, CleaningAction.FIXED),
)


def checks(index: int) -> list[CheckResult]:
    """The four results of a loan, their evidence holding the values ICU orders otherwise."""
    return [
        CheckResult(c.check_type, c.outcome, c.reason, {"note": TRICKY[(index + n) % len(TRICKY)], "n": n})
        for n, c in enumerate(check_results())
    ]


def write_submission(backend: PortBackend) -> None:
    """The same submission, with the same ids, through every stage's writes."""
    user = backend.new_user()
    with backend.owner_units() as unit:
        unit.facilities.upsert_lender(LENDER)
        unit.facilities.upsert_facility(FACILITY)
        unit.commit()
    facts = TapeFacts(
        SUBMISSION, FACILITY.facility_id, TapeKind.PLEDGE, TapeSource.FILE_UPLOAD, "t.csv", "a" * 64,
        "b" * 64, date(2026, 10, 15), 3, user, T0,
    )  # fmt: skip
    loans = [
        Loan.open(
            LoanDraft(loan_id, FACILITY.facility_id, SUBMISSION, LenderLoanNumber(f"SAF-{n}"),
                      LoanTerms(Vin(vin), BorrowerRef("C1234567"), date(2025, 1, 2), Money(1_000_00)),
                      Money(900_00)),
            T0,
        )[0]
        for n, (loan_id, vin) in enumerate(zip(LOAN_IDS, VINS, strict=True))
    ]  # fmt: skip
    with backend.units() as unit:
        unit.tapes.add(TapeSubmission.receive(facts), SUMMIT_LAYOUT)
        unit.raw_rows.add_many(SUBMISSION, raw_rows(SUMMIT_HEADER, 4), T0)
        unit.loans.add_many(loans)
        unit.commit()
    with backend.units() as unit:
        raw = {r.row_number: r.raw_id for r in unit.raw_rows.list_for(SUBMISSION)}
    items = [
        NewItem(uuid.UUID(int=0x20 + n), raw[n + 1], n + 1, RowDisposition.ACCEPTED_NEW, loan_id, ())
        for n, loan_id in enumerate(LOAN_IDS)
    ]
    items.append(NewItem(uuid.UUID(int=0x2F), raw[4], 4, RowDisposition.REJECTED, None, ISSUES))
    with backend.units() as unit:
        unit.items.add_many(SUBMISSION, list(reversed(items)))
        for n, loan_id in enumerate(LOAN_IDS):
            unit.checks.add(SUBMISSION, loan_id, checks(n), T0 + timedelta(minutes=1))
        unit.tokens.add_many({loan_id: tokens(loan_id) for loan_id in LOAN_IDS}, T0 + timedelta(minutes=2))
        for request, loan_id, item_ref in zip(REQUESTS, LOAN_IDS, ITEM_REFS, strict=False):
            unit.requests.add(request, [PlannedItem(item_ref, 0, loan_id)])
        unit.commit()
    locked = ItemOutcome(
        ITEM_REFS[0], hex64("asset").value, "LOCKED", uuid.UUID(int=0x50), False, None, None, (), None, None
    )
    answer = RegistryAnswer(
        201, False, uuid.UUID(int=0x51), uuid.UUID(int=0x52), TapeKind.PLEDGE, KEY_V1, 7,
        uuid.UUID(int=0x53), (locked,), {"note": TRICKY[0], "results": 1},
    )  # fmt: skip
    with backend.units() as unit:
        unit.requests.mark_sent(REQUESTS[0].request_id, T0 + timedelta(minutes=3))
        unit.requests.store_answer(REQUESTS[0].request_id, answer, T0 + timedelta(minutes=4))
        unit.requests.store_result(
            REQUESTS[0].request_id, locked, StoredResult(RegistryItemResult.LOCKED, None)
        )
        unit.commit()


def stage_reads(backend: PortBackend) -> dict[StagePart, tuple[list[DigestRow], str]]:
    """Each stage part's rows as the store returns them, and its digest."""
    write_submission(backend)
    with backend.units() as unit:
        found = {part: unit.history.stage_rows(SUBMISSION, part) for part in StagePart}
    return {part: (rows, rows_sha256(rows)) for part, rows in found.items()}


def test_the_stage_rows_and_digests_are_the_same_in_memory_on_c_and_on_icu() -> None:
    memory = stage_reads(memory_backend())
    with postgres_backend() as backend:
        c_database = stage_reads(backend)
    with postgres_backend(icu=True) as backend:
        icu_database = stage_reads(backend)
    assert all(rows for rows, _ in memory.values()), "every part holds rows"
    assert icu_database == c_database == memory


@pytest.fixture
def icu_bank(
    icu_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> Iterator[BankAgentHarness]:
    seed_reference(icu_db, small_data)
    with BankAgentHarness(icu_db, Services(small_outside, doubles)) as agent:
        for step in manifest_steps(small_data):
            if step.bank_code == "HARBOR":
                agent.ingest(step.file, step.facility_code, step.kind, step.as_of, step.role)
                agent.run_worker()
        agent.sync()
        yield agent


def test_a_harbor_run_on_an_icu_database_verifies_ok(icu_bank: BankAgentHarness) -> None:
    report = icu_bank.verify()
    assert report.status is IntegrityRunStatus.OK, report
    assert report.evidence.problems == ()
