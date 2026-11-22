"""The registry side of one bank: tokenise, submit, certify, alerts, cases, release (SPEC 13 to 15).

Every test runs the bank agent wired by its own composition root against the real
registry and key holder doubles in process (real OPRF, real Ed25519, the real rules),
and reads the outcome back from the bank database and the double's test controls.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date
from pathlib import Path

import oprf
import psycopg
import pytest

from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase, pairs
from bank_testkit.doubles import HolderScalars, InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from bank_testkit.world import CrashError, Faults
from onepledge_bank_agent.application.faults import FaultPoint, NoFaults
from onepledge_bank_agent.domain.enums import IntegrityRunStatus, TapeKind, TapeStatus
from onepledge_bank_agent.domain.integrity import SectionStatus
from onepledge_bank_agent.infrastructure.stop_signal import EventStopSignal

pytestmark = pytest.mark.integration

TAPE = "tapes/HARBOR/S01_HARBOR-SUMMIT_BACK_BOOK.csv"
FACILITY, KIND, AS_OF = "HARBOR-SUMMIT", TapeKind.BACK_BOOK, date(2026, 9, 30)


def test_a_back_book_tape_is_tokenised_submitted_and_certified(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> None:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(small_outside, doubles)) as bank:
        submission = bank.ingest(small_data / TAPE, FACILITY, KIND, AS_OF)
        report = bank.run_worker()
    assert report.runs[-1].status is TapeStatus.COMPLETED, report
    states = pairs(bank_db, "SELECT state, count(*)::text FROM loans GROUP BY 1")
    assert "PENDING" not in states
    locked = int(states.get("LOCKED", "0"))
    assert locked == len(doubles.active_locks("HARBOR"))
    assert submission


def test_an_unknown_message_and_an_alert_for_no_loan_are_audited_and_acknowledged(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> None:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(small_outside, doubles)) as bank:
        doubles.inject_message("HARBOR", "MARKET_NEWS", {"headline": "nothing for the bank"})
        doubles.inject_message(
            "HARBOR",
            "ALERT",
            {
                "case_id": str(uuid.uuid4()),
                "role": "HOLDER",
                "asset_token": "d" * 64,
                "match_kind": "EXACT",
                "match_basis": "ASSET",
                "kinds_matched": ["ASSET"],
                "opened_seq": 4,
            },
        )
        bank.sync()
        bank.sync()
    audited = (
        "SELECT action, count(*)::text FROM audit_log "
        "WHERE action IN ('REGISTRY_MESSAGE_IGNORED', 'ALERT_UNMATCHED') GROUP BY 1"
    )
    assert pairs(bank_db, audited) == {"REGISTRY_MESSAGE_IGNORED": "1", "ALERT_UNMATCHED": "1"}
    ignored = (
        "SELECT detail->>'message_type', outcome FROM audit_log WHERE action = 'REGISTRY_MESSAGE_IGNORED'"
    )
    assert pairs(bank_db, ignored) == {"MARKET_NEWS": "SUCCESS"}
    assert pairs(bank_db, "SELECT 'alerts', count(*)::text FROM alerts") == {"alerts": "0"}


def test_a_release_tape_releases_its_locked_loans_at_the_registry(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> None:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(small_outside, doubles)) as bank:
        bank.ingest(small_data / TAPE, FACILITY, KIND, AS_OF)
        bank.run_worker()
        locks = len(doubles.active_locks("HARBOR"))
        release = small_data / "tapes/HARBOR/S11_HARBOR-SUMMIT_RELEASE.csv"
        submission = bank.ingest(release, FACILITY, TapeKind.RELEASE, date(2026, 11, 12))
        assert bank.run_worker().runs[-1].status is TapeStatus.COMPLETED
    released = pairs(bank_db, "SELECT 'released', count(*)::text FROM loans WHERE state = 'RELEASED'")
    assert int(released["released"]) == locks - len(doubles.active_locks("HARBOR")) > 0
    results = (
        "SELECT i.result, count(*)::text FROM registry_request_items i "
        "JOIN registry_requests r USING (request_id) WHERE r.submission_id = %s GROUP BY 1"
    )
    assert set(pairs(bank_db, results, (submission.submission_id,))) <= {"RELEASED", "REJECTED"}
    tokenised = (
        "SELECT 'unmatched', count(*)::text FROM loans l WHERE state <> 'HELD_LOCAL' "
        "AND (SELECT count(*) FROM loan_tokens t WHERE t.loan_id = l.loan_id) <> 8"
    )
    assert pairs(bank_db, tokenised) == {"unmatched": "0"}, "every submitted loan has its eight codes"


def direct_tokens(scalars: HolderScalars, terms: tuple[object, ...]) -> dict[str, str]:
    """The eight tokens of (LEI, VIN, borrower, cents, ISO day) by direct evaluation `k2 * k1 * H(input)`,
    written from SPEC 9.2 and 9.3 alone."""
    lei, vin, borrower, amount, day = (str(term) for term in terms)
    party, cents = f"{lei}|{borrower}", int(amount)
    values = {
        "ASSET": f"AUTO|{vin}", "VIN": vin, "BORROWER": party, "AMOUNT_EXACT": f"{party}|{cents}",
        "AMOUNT_100": f"{party}|{cents // 10_000}", "AMOUNT_1000": f"{party}|{cents // 100_000}",
        "DATE_DAY": f"{party}|{day}", "DATE_MONTH": f"{party}|{day[:7]}",
    }  # fmt: skip
    tokens = {}
    for kind, value in values.items():
        point = oprf.data.hash(b"onepledge:v1:" + kind.encode() + b"\x1f" + value.encode())
        evaluated = bytes(oprf.mask(scalars.kh2).mask(oprf.mask(scalars.kh1).mask(point)))
        material = b"onepledge:v1:token\x1f" + kind.encode() + b"\x1f" + evaluated
        tokens[kind] = hashlib.sha256(material).hexdigest()
    return tokens


def test_stored_tokens_equal_an_independent_direct_evaluation_with_the_holders_scalars(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> None:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(small_outside, doubles)) as bank:
        bank.ingest(small_data / TAPE, FACILITY, KIND, AS_OF)
        bank.run_worker()
    with bank_db.connect("admin") as connection:
        loans = connection.execute(
            "SELECT l.loan_id, le.lei, l.vin, l.borrower_ref, l.original_amount_cents, "
            "l.origination_date::text FROM loans l JOIN facilities f USING (facility_id) "
            "JOIN lenders le USING (lender_id) "
            "WHERE l.state = 'LOCKED' ORDER BY l.loan_id LIMIT 3"
        ).fetchall()
        assert len(loans) == 3
        for loan_id, *terms in loans:
            terms_of_loan = tuple(terms)
            rows = connection.execute(
                "SELECT token_kind, token FROM loan_tokens WHERE loan_id = %s AND key_version = '1.1'",
                (loan_id,),
            ).fetchall()
            stored = {str(kind): str(token) for kind, token in rows}
            assert stored == direct_tokens(doubles.scalars, terms_of_loan)


class StopOnFirstAck(NoFaults):
    """Requests a stop on `signal`, as SIGTERM would, while the first outbox message is acknowledged."""

    def __init__(self) -> None:
        self.signal = EventStopSignal()

    def reached(self, point: FaultPoint) -> None:
        if point is FaultPoint.BETWEEN_OUTBOX_COMMIT_AND_ACK:
            self.signal.request_stop()


def test_a_stop_ends_the_outbox_sync_after_the_acknowledged_message(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> None:
    """SPEC_DECISIONS 51: the outbox message is the unit; the rest waits for the next start."""
    seed_reference(bank_db, small_data)
    ignored = "SELECT 'ignored', count(*)::text FROM audit_log WHERE action = 'REGISTRY_MESSAGE_IGNORED'"
    stop, services = StopOnFirstAck(), Services(small_outside, doubles)
    with BankAgentHarness(bank_db, services, faults=stop) as stopping:
        stop.signal = stopping.stop
        for number in range(3):
            doubles.inject_message("HARBOR", "MARKET_NEWS", {"headline": f"note {number}"})
        first = stopping.composition.outbox_sync().sync()
    assert first.messages_processed == 1
    assert int(pairs(bank_db, ignored)["ignored"]) <= 1
    with BankAgentHarness(bank_db, services) as resumed:
        rest = resumed.composition.outbox_sync().sync()
        assert resumed.composition.outbox_sync().sync().messages_processed == 0
    assert rest.messages_processed >= 2
    assert pairs(bank_db, ignored) == {"ignored": "3"}


ALERT_PAYLOAD = {
    "case_id": "6f1c1e9e-6d3e-4a43-9b52-0d4c6a7c2a10",
    "role": "HOLDER",
    "asset_token": "d" * 64,
    "match_kind": "EXACT",
    "match_basis": "ASSET",
    "kinds_matched": ["ASSET"],
    "opened_seq": 4,
}
QUARANTINED = (
    "SELECT message_type || ' ' || reason, (payload IS NULL)::text || ' ' || payload_sha256 "
    "FROM registry_messages_quarantined"
)


class CrashAfterFirstQuarantine(Faults):
    """Crashes between commit and acknowledgement of the first quarantined message, once."""

    def __init__(self, database: BankDatabase) -> None:
        super().__init__()
        self._database = database
        self.crashed = False

    def reached(self, point: FaultPoint) -> None:
        super().reached(point)
        held = pairs(self._database, "SELECT 'rows', count(*)::text FROM registry_messages_quarantined")
        if point is FaultPoint.BETWEEN_OUTBOX_COMMIT_AND_ACK and not self.crashed and held == {"rows": "1"}:
            self.crashed = True
            raise CrashError(point.value)


def test_a_known_message_with_a_payload_outside_the_contract_is_quarantined_and_the_outbox_moves_on(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> None:
    """SPEC_DECISIONS 57: each reason is stored, audited as a FAILURE, acknowledged, and reported."""
    seed_reference(bank_db, small_data)
    faults = CrashAfterFirstQuarantine(bank_db)
    with BankAgentHarness(bank_db, Services(small_outside, doubles), faults=faults) as bank:
        doubles.inject_message("HARBOR", "ALERT", {**ALERT_PAYLOAD, "asset_token": "A" * 64})
        doubles.inject_message("HARBOR", "ALERT", {**ALERT_PAYLOAD, "opened_seq": 1.5})
        doubles.inject_message("HARBOR", "CASE_UPDATE", {"case_id": "x", "notes": "n" * 70_000})
        doubles.inject_message("HARBOR", "MARKET_NEWS", {"headline": "an unknown type is not a fault"})
        doubles.inject_message("HARBOR", "ALERT", ALERT_PAYLOAD)
        with pytest.raises(CrashError):
            bank.sync()
        bank.sync()
        bank.sync()
        report = bank.report()
    assert faults.crashed, "the first quarantined message was delivered again"
    found = pairs(bank_db, QUARANTINED)
    assert sorted(found) == ["ALERT SCHEMA_MISMATCH", "CASE_UPDATE OVERSIZED"]
    counted = (
        "SELECT message_type || ' ' || reason, count(*)::text FROM registry_messages_quarantined GROUP BY 1"
    )
    assert pairs(bank_db, counted) == {"ALERT SCHEMA_MISMATCH": "2", "CASE_UPDATE OVERSIZED": "1"}, (
        "a redelivery after a crash stored nothing twice; capital letters are a schema mismatch (decision 85)"
    )
    assert found["CASE_UPDATE OVERSIZED"].startswith("true "), "an oversized payload keeps only its hash"
    assert all(value.startswith("false ") for key, value in found.items() if "OVERSIZED" not in key)
    audited = (
        "SELECT detail->>'message_type' || ' ' || (detail->>'reason'), outcome || ' ' || object_type "
        "FROM audit_log WHERE action = 'REGISTRY_MESSAGE_QUARANTINED'"
    )
    assert pairs(bank_db, audited) == dict.fromkeys(found, "FAILURE REGISTRY_MESSAGE")
    anchored = (
        "SELECT count(*)::text, 'n' FROM audit_log a JOIN registry_messages_quarantined q "
        "ON q.quarantine_id = a.object_id WHERE a.action = 'REGISTRY_MESSAGE_QUARANTINED'"
    )
    assert pairs(bank_db, anchored) == {"3": "n"}, "each audit entry names its quarantine row"
    hashes = (
        "SELECT detail->>'payload_sha256', 'audited' FROM audit_log "
        "WHERE action = 'REGISTRY_MESSAGE_QUARANTINED'"
    )
    stored = "SELECT payload_sha256, 'stored' FROM registry_messages_quarantined"
    assert sorted(pairs(bank_db, hashes)) == sorted(pairs(bank_db, stored))
    others = (
        "SELECT action, count(*)::text FROM audit_log "
        "WHERE action IN ('REGISTRY_MESSAGE_IGNORED', 'ALERT_UNMATCHED') GROUP BY 1"
    )
    assert pairs(bank_db, others) == {"REGISTRY_MESSAGE_IGNORED": "1", "ALERT_UNMATCHED": "1"}
    assert (report.registry_messages.status, report.registry_messages.quarantined) == (
        SectionStatus.BROKEN,
        3,
    )
    assert report.status is IntegrityRunStatus.BROKEN
    acknowledged = [sent for sent in doubles.sent if sent.path.endswith("/ack")]
    assert len(acknowledged) >= 5


def test_the_quarantine_table_is_insert_only_and_the_app_role_may_only_read_and_append(
    bank_db: BankDatabase,
) -> None:
    grants = (
        "SELECT privilege_type, 'granted' FROM information_schema.role_table_grants "
        "WHERE table_name = 'registry_messages_quarantined' AND grantee = %s"
    )
    assert pairs(bank_db, grants, (bank_db.app_role,)) == {"SELECT": "granted", "INSERT": "granted"}
    with bank_db.connect("owner") as connection:
        for statement in (
            "UPDATE registry_messages_quarantined SET reason = 'OVERSIZED'",
            "DELETE FROM registry_messages_quarantined",
            "TRUNCATE registry_messages_quarantined",
        ):
            with pytest.raises(psycopg.Error) as refused:
                connection.execute(statement)
            assert refused.value.sqlstate == "OP001", statement
