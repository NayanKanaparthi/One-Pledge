"""Deep verification finds every edit of a decision the bank acts on (SPEC_DECISIONS 107; review 3 quality 2).

Each tamper of review 3 quality finding 2 is made as a superuser with the relevant triggers
switched off and on again, then `integrity verify` must name it: the stage digests of (a) the
table, the submission and the stage; the re-derivation of (b) the row or the loan; the replay
of (c) the loan, tape, alert or user. A clean bank verifies OK first, so every digest a stage
stored is the digest the tables give.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import LiteralString

import pytest
from psycopg import sql

from bank_testkit.banks import manifest_steps
from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase
from bank_testkit.doubles import InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from onepledge_bank_agent.domain.enums import IntegrityRunStatus, TapeStatus
from onepledge_bank_agent.domain.integrity import EvidenceProblemKind, IntegrityReport, SectionStatus

pytestmark = pytest.mark.integration

BANK = "HARBOR"
DIGEST = EvidenceProblemKind.DIGEST_MISMATCH
DERIVED = EvidenceProblemKind.DERIVATION_MISMATCH
STATE = EvidenceProblemKind.STATE_MISMATCH


@dataclass
class Bank:
    database: BankDatabase
    agent: BankAgentHarness
    doubles: InProcessDoubles

    def one(self, query: LiteralString, *parameters: object) -> tuple[object, ...]:
        with self.database.connect("admin") as connection:
            found = connection.execute(query, parameters).fetchone()
        assert found is not None, query
        return tuple(found)

    def tamper(self, tables: tuple[str, ...], *statements: tuple[LiteralString, tuple[object, ...]]) -> None:
        """Run the statements as a superuser with every trigger of the tables off, then on again."""
        with self.database.connect("admin") as connection:
            for table in tables:
                connection.execute(
                    sql.SQL("ALTER TABLE {} DISABLE TRIGGER ALL").format(sql.Identifier(table))
                )
            for statement, parameters in statements:
                connection.execute(statement, parameters)
            for table in tables:
                connection.execute(sql.SQL("ALTER TABLE {} ENABLE TRIGGER ALL").format(sql.Identifier(table)))

    def found(self) -> set[tuple[str, str | None, EvidenceProblemKind, TapeStatus | None]]:
        report: IntegrityReport = self.agent.verify()
        assert report.status is IntegrityRunStatus.BROKEN and report.evidence.status is SectionStatus.BROKEN
        return {(p.table, p.row_id, p.problem, p.stage) for p in report.evidence.problems}


@pytest.fixture
def bank(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> Iterator[Bank]:
    seed_reference(bank_db, small_data)
    with BankAgentHarness(bank_db, Services(small_outside, doubles)) as agent:
        for step in manifest_steps(small_data):
            if step.bank_code == BANK:
                agent.ingest(step.file, step.facility_code, step.kind, step.as_of, step.role)
                agent.run_worker()
        agent.sync()
        assert agent.verify().status is IntegrityRunStatus.OK, "a clean bank verifies OK first"
        yield Bank(bank_db, agent, doubles)


def submission_of_check(bank: Bank, result: LiteralString) -> tuple[str, str]:
    check_id, submission = bank.one(
        "SELECT check_id::text, submission_id::text FROM local_checks WHERE result LIKE %s ORDER BY check_id "
        "LIMIT 1",
        result,
    )
    return str(check_id), str(submission)


def test_a_check_flipped_to_pass_is_a_digest_mismatch_of_its_stage(bank: Bank) -> None:
    check_id, submission = submission_of_check(bank, "FAIL")
    result, reason = bank.one(
        "SELECT result, reason_code FROM local_checks WHERE result = 'PASS' AND check_type = "
        "(SELECT check_type FROM local_checks WHERE check_id = %s) LIMIT 1",
        check_id,
    )
    update = "UPDATE local_checks SET result = %s, reason_code = %s WHERE check_id = %s"
    bank.tamper(("local_checks",), (update, (result, reason, check_id)))
    assert ("local_checks", submission, DIGEST, TapeStatus.CHECKED) in bank.found()


def test_a_deleted_check_is_a_digest_mismatch_of_its_stage(bank: Bank) -> None:
    check_id, submission = submission_of_check(bank, "%")
    bank.tamper(("local_checks",), ("DELETE FROM local_checks WHERE check_id = %s", (check_id,)))
    assert ("local_checks", submission, DIGEST, TapeStatus.CHECKED) in bank.found()


def tokenised_loan(bank: Bank) -> tuple[str, str]:
    loan, submission = bank.one(
        "SELECT i.loan_id::text, r.submission_id::text FROM registry_request_items i "
        "JOIN registry_requests r USING (request_id) WHERE r.origin = 'TAPE' AND i.result = 'LOCKED' "
        "ORDER BY r.responded_at LIMIT 1"
    )
    return str(loan), str(submission)


def test_a_changed_token_is_a_digest_mismatch_of_the_submit_stage(bank: Bank) -> None:
    loan, submission = tokenised_loan(bank)
    update = "UPDATE loan_tokens SET token = repeat('e', 64) WHERE loan_id = %s AND token_kind = 'ASSET'"
    bank.tamper(("loan_tokens",), (update, (loan,)))
    assert ("loan_tokens", submission, DIGEST, TapeStatus.SUBMITTED) in bank.found()


def test_a_loans_deleted_tokens_are_a_digest_mismatch_of_the_submit_stage(bank: Bank) -> None:
    loan, submission = tokenised_loan(bank)
    bank.tamper(("loan_tokens",), ("DELETE FROM loan_tokens WHERE loan_id = %s", (loan,)))
    assert ("loan_tokens", submission, DIGEST, TapeStatus.SUBMITTED) in bank.found()


def test_a_changed_issue_action_is_a_digest_and_a_derivation_mismatch(bank: Bank) -> None:
    issue, action, submission, row_number = bank.one(
        "SELECT i.issue_id::text, i.action, r.submission_id::text, r.row_number FROM cleaning_issues i "
        "JOIN loans_raw r USING (raw_id) WHERE i.action = 'FIXED' ORDER BY i.issue_id LIMIT 1"
    )
    assert action == "FIXED"
    bank.tamper(
        ("cleaning_issues",), ("UPDATE cleaning_issues SET action = 'REJECTED' WHERE issue_id = %s", (issue,))
    )
    found = bank.found()
    assert ("cleaning_issues", submission, DIGEST, TapeStatus.CLEANED) in found
    assert ("cleaning_issues", f"{submission}/{row_number}", DERIVED, None) in found


def test_a_loan_state_changed_with_the_guard_off_is_a_state_mismatch(bank: Bank) -> None:
    (loan,) = bank.one("SELECT loan_id::text FROM loans WHERE state = 'HELD_LOCAL' ORDER BY loan_id LIMIT 1")
    bank.tamper(("loans",), ("UPDATE loans SET state = 'PENDING' WHERE loan_id = %s", (loan,)))
    assert ("loans", loan, STATE, None) in bank.found()


def test_a_changed_loan_amount_is_a_derivation_mismatch(bank: Bank) -> None:
    loan, _ = tokenised_loan(bank)
    update = "UPDATE loans SET original_amount_cents = original_amount_cents + 1 WHERE loan_id = %s"
    bank.tamper(("loans",), (update, (loan,)))
    assert ("loans", loan, DERIVED, None) in bank.found()


def test_a_deleted_alert_is_missing_and_its_replay_has_no_row(bank: Bank) -> None:
    (token,) = bank.one("SELECT token FROM loan_tokens WHERE token_kind = 'ASSET' ORDER BY loan_id LIMIT 1")
    case = {"case_id": str(uuid.uuid4()), "role": "HOLDER", "asset_token": token, "match_kind": "EXACT",
            "match_basis": "ASSET", "kinds_matched": ["ASSET"], "opened_seq": 4}  # fmt: skip
    bank.doubles.inject_message(BANK, "ALERT", case)
    assert bank.agent.sync().complete
    assert bank.agent.verify().status is IntegrityRunStatus.OK, "an alert replays to its row"
    (alert,) = bank.one("SELECT alert_id::text FROM alerts ORDER BY alert_id LIMIT 1")
    bank.tamper(("alerts",), ("DELETE FROM alerts WHERE alert_id = %s", (alert,)))
    found = bank.found()
    assert ("alerts", alert, EvidenceProblemKind.ROW_MISSING, None) in found
    assert ("alerts", alert, STATE, None) in found


def test_a_held_loan_deleted_with_its_items_is_found_by_replay_and_digests(bank: Bank) -> None:
    loan, submission = bank.one(
        "SELECT l.loan_id::text, i.submission_id::text FROM loans l JOIN submission_items i USING (loan_id) "
        "WHERE l.state = 'HELD_LOCAL' AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.loan_id = l.loan_id) "
        "ORDER BY l.loan_id LIMIT 1"
    )
    tables = ("submission_items", "local_checks", "loan_tokens", "loans")
    bank.tamper(
        tables,
        ("DELETE FROM submission_items WHERE loan_id = %s", (loan,)),
        ("DELETE FROM local_checks WHERE loan_id = %s", (loan,)),
        ("DELETE FROM loan_tokens WHERE loan_id = %s", (loan,)),
        ("DELETE FROM loans WHERE loan_id = %s", (loan,)),
    )
    found = bank.found()
    assert ("loans", loan, STATE, None) in found
    assert ("submission_items", submission, DIGEST, TapeStatus.CLEANED) in found
    assert ("local_checks", submission, DIGEST, TapeStatus.CHECKED) in found


def test_a_changed_item_result_is_a_digest_mismatch_of_the_submit_stage(bank: Bank) -> None:
    loan, submission = tokenised_loan(bank)
    update = "UPDATE registry_request_items SET result = 'CONFLICT' WHERE loan_id = %s AND result = 'LOCKED'"
    bank.tamper(("registry_request_items",), (update, (loan,)))
    assert ("registry_requests", submission, DIGEST, TapeStatus.SUBMITTED) in bank.found()


def test_a_replaced_response_body_is_a_digest_mismatch_of_the_submit_stage(bank: Bank) -> None:
    request, submission = bank.one("SELECT request_id::text, submission_id::text FROM registry_requests "
                                   "WHERE origin = 'TAPE' ORDER BY responded_at LIMIT 1")  # fmt: skip
    update = "UPDATE registry_requests SET response_body = '{\"results\": []}' WHERE request_id = %s"
    bank.tamper(("registry_requests",), (update, (request,)))
    assert ("registry_requests", submission, DIGEST, TapeStatus.SUBMITTED) in bank.found()


def test_changed_reference_data_is_a_digest_mismatch_of_its_table(bank: Bank) -> None:
    bank.tamper(("facilities",), ("UPDATE facilities SET credit_limit_cents = credit_limit_cents + 1", ()))
    found = bank.found()
    assert ("facilities", None, DIGEST, None) in found and ("lenders", None, DIGEST, None) not in found


def test_a_changed_user_role_is_a_state_mismatch(bank: Bank) -> None:
    (user,) = bank.one("SELECT user_id::text FROM users WHERE role = 'ANALYST' LIMIT 1")
    bank.tamper(("users",), ("UPDATE users SET role = 'ADMIN' WHERE user_id = %s", (user,)))
    assert ("users", user, STATE, None) in bank.found()


def test_a_tape_status_changed_with_the_guard_off_is_a_state_mismatch(bank: Bank) -> None:
    (tape,) = bank.one("SELECT submission_id::text FROM tape_submissions WHERE status = 'COMPLETED' LIMIT 1")
    update = "UPDATE tape_submissions SET status = 'SUBMITTED', completed_at = NULL WHERE submission_id = %s"
    bank.tamper(("tape_submissions",), (update, (tape,)))
    assert ("tape_submissions", tape, STATE, None) in bank.found()


def test_a_second_clean_verification_still_finds_nothing(bank: Bank) -> None:
    """Recomputing changes nothing: the digests, re-derivations and replays agree with the tables."""
    assert bank.agent.verify().evidence.problems == ()
