"""The registry side of one bank: tokenise, submit, certify, alerts, cases, release (SPEC 13 to 15).

Every test runs the bank agent wired by its own composition root against the real
registry and key holder doubles in process (real OPRF, real Ed25519, the real rules),
and reads the outcome back from the bank database and the double's test controls.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest

from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase, pairs
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from onepledge_bank_agent.domain.enums import TapeKind, TapeStatus

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
