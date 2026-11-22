"""The registry side of one bank: tokenise, submit, certify, alerts, cases, release (SPEC 13 to 15).

Every test runs the bank agent wired by its own composition root against the real
registry and key holder doubles in process (real OPRF, real Ed25519, the real rules),
and reads the outcome back from the bank database and the double's test controls.
"""

from __future__ import annotations

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
