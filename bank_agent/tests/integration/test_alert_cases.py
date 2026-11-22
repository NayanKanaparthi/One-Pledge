"""Alerts, case positions and resubmission across banks (SPEC 14.2 to 14.5), after the small replay.

A conflict planted by the data set is reviewed at both banks; equal positions resolve
the case at the registry, both alerts clear (one from the position's answer, one from
the CASE_UPDATE message), and the claimant's loan, once every alert on it is cleared,
is resubmitted and locked. A repeated or out of turn action is refused with its code.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from bank_testkit.banks import ThreeBanks
from bank_testkit.database import BankDatabase
from bank_testkit.doubles import REGISTRY_URL
from bank_testkit.outside import InProcessOutsideRecords
from onepledge_bank_agent.application.review_alert import ReviewAction
from onepledge_bank_agent.domain.dependency_errors import RegistryIntegrityBrokenError
from onepledge_bank_agent.domain.enums import AlertStatus, BankPosition, CaseStatus, LoanState
from onepledge_bank_agent.domain.errors import (
    AlertTransitionInvalidError,
    LoanNotResubmittableError,
    PositionAlreadyRecordedError,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def banks(
    new_database: Callable[[], BankDatabase], small_data: Path, small_outside: InProcessOutsideRecords
) -> Iterator[ThreeBanks]:
    with ThreeBanks(new_database, small_data, small_outside) as world:
        world.replay()
        yield world


def one(database: BankDatabase, query: str, parameters: tuple[object, ...] = ()) -> tuple[Any, ...]:
    with database.connect("admin") as connection:
        row = connection.execute(query, parameters).fetchone()
    assert row is not None, query
    return tuple(row)


def open_case(banks: ThreeBanks, basis: str) -> tuple[dict[str, Any], str, str]:
    """An open case of the given match basis, with the bank codes of its holder and claimant."""
    member_of = {member.member_id: code for code, member in banks.doubles.members.items()}
    cases = banks.doubles.control(REGISTRY_URL, "GET", "/_test/dump")["cases"]
    case = next(c for c in cases if c["status"] == "OPEN" and c["match_basis"] == basis)
    return case, member_of[case["holder_member_id"]], member_of[case["claimant_member_id"]]


def alert_of(banks: ThreeBanks, code: str, case_id: str) -> tuple[uuid.UUID, uuid.UUID]:
    alert_id, loan_id = one(
        banks.databases[code], "SELECT alert_id, loan_id FROM alerts WHERE registry_case_id = %s", (case_id,)
    )
    return alert_id, loan_id


def test_equal_positions_clear_both_alerts_and_the_cleared_loan_is_resubmitted_and_locked(
    banks: ThreeBanks,
) -> None:
    case, holder, claimant = open_case(banks, "FIELDS")
    claimant_alert, claimant_loan = alert_of(banks, claimant, case["case_id"])
    holder_alert, _ = alert_of(banks, holder, case["case_id"])
    claimant_agent, holder_agent = banks.agents[claimant], banks.agents[holder]
    assert one(banks.databases[claimant], "SELECT state FROM loans WHERE loan_id = %s", (claimant_loan,)) == (
        "CONFLICT",
    )
    with pytest.raises(LoanNotResubmittableError):
        claimant_agent.resubmit(claimant_loan)
    with pytest.raises(AlertTransitionInvalidError):
        claimant_agent.review(claimant_alert, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)

    assert claimant_agent.review(claimant_alert, ReviewAction.START_REVIEW).status is AlertStatus.REVIEWING
    with pytest.raises(AlertTransitionInvalidError):
        claimant_agent.review(claimant_alert, ReviewAction.START_REVIEW)
    waiting = claimant_agent.review(claimant_alert, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)
    assert (waiting.status, waiting.record.case_status) == (AlertStatus.REVIEWING, CaseStatus.OPEN)
    assert waiting.record.review.bank_position is BankPosition.DATA_ERROR
    with pytest.raises(PositionAlreadyRecordedError):
        claimant_agent.review(
            claimant_alert, ReviewAction.SUBMIT_POSITION, BankPosition.CONFIRMED_DOUBLE_PLEDGE
        )

    holder_agent.review(holder_alert, ReviewAction.START_REVIEW)
    resolved = holder_agent.review(holder_alert, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)
    assert (resolved.status, resolved.record.case_status) == (
        AlertStatus.CLEARED,
        CaseStatus.CLEARED_DATA_ERROR,
    )
    claimant_agent.sync()
    updated = one(
        banks.databases[claimant],
        "SELECT status, case_status FROM alerts WHERE alert_id = %s",
        (claimant_alert,),
    )
    assert updated == ("CLEARED", "CLEARED_DATA_ERROR")
    claimant_agent.sync()
    audited = one(
        banks.databases[claimant],
        "SELECT count(*) FROM audit_log WHERE action = 'ALERT_CASE_UPDATED' AND object_id = %s",
        (claimant_alert,),
    )
    assert audited == (1,), "a redelivered or repeated update changes nothing"

    locks = len(banks.doubles.active_locks(claimant))
    submissions = banks.doubles.member_stats(claimant)["submissions"]
    banks.doubles.tamper(mode="BAD_CHECKPOINT_SIGNATURE")
    claimant_agent.sync()
    with pytest.raises(RegistryIntegrityBrokenError):
        claimant_agent.resubmit(claimant_loan)
    assert banks.doubles.member_stats(claimant)["submissions"] == submissions, "the halt rule sent nothing"
    with banks.agent_with(claimant, environment={"HALT_ON_REGISTRY_INTEGRITY_FAILURE": "false"}) as operator:
        loan = operator.resubmit(claimant_loan)
        with pytest.raises(LoanNotResubmittableError):
            operator.resubmit(claimant_loan)
    assert loan.state is LoanState.LOCKED
    assert len(banks.doubles.active_locks(claimant)) == locks + 1
    request = one(
        banks.databases[claimant],
        "SELECT r.origin, r.kind, r.item_count, c.verified FROM registry_requests r "
        "JOIN certificates c USING (request_id) JOIN registry_request_items i USING (request_id) "
        "WHERE i.loan_id = %s AND r.origin = 'RESUBMIT'",
        (claimant_loan,),
    )
    assert request == ("RESUBMIT", "PLEDGE", 1, True)
    resubmitted = "SELECT count(*) FROM audit_log WHERE action = 'LOAN_RESUBMITTED' AND object_id = %s"
    assert one(banks.databases[claimant], resubmitted, (claimant_loan,)) == (1,)


def test_a_confirmed_double_pledge_escalates_both_alerts_and_keeps_the_loans(banks: ThreeBanks) -> None:
    case, holder, claimant = open_case(banks, "ASSET")
    claimant_alert, claimant_loan = alert_of(banks, claimant, case["case_id"])
    holder_alert, holder_loan = alert_of(banks, holder, case["case_id"])
    for code, alert in ((claimant, claimant_alert), (holder, holder_alert)):
        banks.agents[code].review(alert, ReviewAction.START_REVIEW)
        banks.agents[code].review(alert, ReviewAction.SUBMIT_POSITION, BankPosition.CONFIRMED_DOUBLE_PLEDGE)
    banks.sync_all()
    for code, alert, loan, state in (
        (claimant, claimant_alert, claimant_loan, "CONFLICT"),
        (holder, holder_alert, holder_loan, "LOCKED"),
    ):
        status = one(
            banks.databases[code], "SELECT status, case_status FROM alerts WHERE alert_id = %s", (alert,)
        )
        assert status == ("ESCALATED", "CONFIRMED"), code
        assert one(banks.databases[code], "SELECT state FROM loans WHERE loan_id = %s", (loan,)) == (state,)
    with pytest.raises(LoanNotResubmittableError):
        banks.agents[claimant].resubmit(claimant_loan)


def test_a_quarantined_checkpoint_message_halts_case_positions_and_resubmissions(banks: ThreeBanks) -> None:
    """SPEC_DECISIONS 56 and 57: a CHECKPOINT message that breaks the contract activates the halt rule."""
    case, _, claimant = open_case(banks, "ASSET")
    alert, loan = alert_of(banks, claimant, case["case_id"])
    agent, database = banks.agents[claimant], banks.databases[claimant]
    agent.review(alert, ReviewAction.START_REVIEW)
    broken = {"body": {"checkpoint_seq": "one"}, "signature": "not base64", "signing_key_id": "registry-1"}
    banks.doubles.inject_message(claimant, "CHECKPOINT", broken)
    agent.sync()
    assert one(database, "SELECT message_type, reason FROM registry_messages_quarantined") == (
        "CHECKPOINT",
        "SCHEMA_MISMATCH",
    )
    sent = len(banks.doubles.sent)
    with pytest.raises(RegistryIntegrityBrokenError):
        agent.review(alert, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)
    with pytest.raises(RegistryIntegrityBrokenError):
        agent.resubmit(loan)
    assert banks.doubles.sent[sent:] == [], "nothing was sent to the registry"
    assert one(database, "SELECT status, bank_position FROM alerts WHERE alert_id = %s", (alert,)) == (
        "REVIEWING",
        None,
    )
    with banks.agent_with(claimant, environment={"HALT_ON_REGISTRY_INTEGRITY_FAILURE": "false"}) as operator:
        recorded = operator.review(alert, ReviewAction.SUBMIT_POSITION, BankPosition.DATA_ERROR)
    assert recorded.record.review.bank_position is BankPosition.DATA_ERROR
