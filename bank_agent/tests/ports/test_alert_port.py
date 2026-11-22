"""`AlertRepository`: alerts keyed by case and loan, with their review fields and statuses."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace

import pytest

from bank_testkit.backends import PortWorld
from bank_testkit.memory.bank import vins
from bank_testkit.records import alert, at
from onepledge_bank_agent.application.ports import UnitOfWork
from onepledge_bank_agent.domain.alert import Alert
from onepledge_bank_agent.domain.enums import AlertStatus, BankPosition, CaseStatus
from onepledge_bank_agent.domain.store_errors import StoreRefusedError


def add(ports: PortWorld, *alerts: Alert) -> None:
    """Insert alerts and commit."""
    with ports.units() as unit:
        for made in alerts:
            unit.alerts.add(made)
        unit.commit()


class TestAlerts:
    """`alerts`."""

    def test_an_alert_reads_back_by_id_and_by_case_and_loan(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        loan, other = ports.loans(tape.submission_id, vins(2))
        made = alert(loan.loan_id)
        add(ports, made)
        case = made.record.registry_case_id
        with ports.units() as unit:
            by_id = unit.alerts.get(made.record.alert_id, for_update=True)
            by_case = unit.alerts.find(case, loan.loan_id)
            assert unit.alerts.get(uuid.uuid4()) is None
            assert unit.alerts.find(case, other.loan_id) is None
            assert unit.alerts.find(uuid.uuid4(), loan.loan_id) is None
        assert by_id is not None and by_id.record == made.record
        assert by_case is not None and by_case.record == made.record

    def test_a_cases_alerts_are_those_on_the_given_loans_in_id_order(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        loans = ports.loans(tape.submission_id, vins(3))
        case = uuid.uuid4()
        made = [alert(loan.loan_id, case) for loan in loans]
        elsewhere = alert(loans[0].loan_id)
        add(ports, *made, elsewhere)
        wanted = [loans[2].loan_id, loans[0].loan_id]
        with ports.units() as unit:
            found = unit.alerts.for_case(case, wanted)
            assert unit.alerts.for_case(case, []) == []
        expected = sorted((made[0], made[2]), key=lambda a: a.record.alert_id)
        assert [a.record for a in found] == [a.record for a in expected]

    def test_a_loans_alerts_are_listed_oldest_first(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        loan, other = ports.loans(tape.submission_id, vins(2))
        newest, oldest, middle = (
            alert(loan.loan_id, received=at(9)),
            alert(loan.loan_id, received=at(2)),
            alert(loan.loan_id, received=at(5)),
        )
        add(ports, newest, oldest, middle, alert(other.loan_id))
        with ports.units() as unit:
            found = unit.alerts.for_loan(loan.loan_id)
            assert unit.alerts.for_loan(uuid.uuid4()) == []
        assert [a.record for a in found] == [oldest.record, middle.record, newest.record]

    def test_save_stores_the_review_the_position_and_the_case_status(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        (loan,) = ports.loans(tape.submission_id, vins(1))
        made = alert(loan.loan_id)
        add(ports, made)
        moves = (
            lambda: made.start_review(ports.user, at(2), "looking"),
            lambda: made.submit_position(BankPosition.DATA_ERROR, ports.user, at(3)),
            lambda: made.apply_case_status(CaseStatus.CLEARED_DATA_ERROR),
        )
        for move in moves:  # each saved on its own, as each review action and case update saves it
            move()
            with ports.units() as unit:
                unit.alerts.save(made)
                unit.commit()
            with ports.units() as unit:
                stored = unit.alerts.get(made.record.alert_id)
            assert stored is not None and stored.record == made.record
        with ports.units() as unit:
            found = unit.alerts.get(made.record.alert_id)
        assert found is not None and found.record == made.record
        review = found.record.review
        assert (found.status, found.record.case_status) == (
            AlertStatus.CLEARED,
            CaseStatus.CLEARED_DATA_ERROR,
        )
        assert (review.reviewed_by, review.note, review.bank_position, review.position_submitted_at) == (
            ports.user, "looking", BankPosition.DATA_ERROR, at(3),
        )  # fmt: skip


class TestAlertRefusals:
    """What both stores refuse for an alert (SPEC_DECISIONS 86, 87, 92)."""

    @pytest.mark.parametrize(
        ("status", "case"),
        [
            (AlertStatus.CLEARED, CaseStatus.OPEN),
            (AlertStatus.CLEARED, CaseStatus.CONFIRMED),
            (AlertStatus.ESCALATED, CaseStatus.CLEARED_DATA_ERROR),
        ],
    )
    def test_an_alert_closed_without_its_case_is_refused(
        self, ports: PortWorld, status: AlertStatus, case: CaseStatus
    ) -> None:
        made = self._stored(ports)
        forged = Alert(replace(made.record, status=status, case_status=case))
        self._refused(ports, lambda unit: unit.alerts.save(forged), "OP006")

    def test_a_closed_alert_never_changes_and_a_position_is_recorded_once(self, ports: PortWorld) -> None:
        made = self._stored(ports)
        made.start_review(ports.user, at(2))
        self._save(ports, made)
        made.submit_position(BankPosition.DATA_ERROR, ports.user, at(3))
        self._save(ports, made)
        changed = replace(made.record.review, bank_position=BankPosition.CONFIRMED_DOUBLE_PLEDGE)
        self._refused(
            ports, lambda unit: unit.alerts.save(Alert(replace(made.record, review=changed))), "OP006"
        )
        made.apply_case_status(CaseStatus.CLEARED_DATA_ERROR)
        self._save(ports, made)
        noted = replace(made.record.review, note="after the fact")
        self._refused(
            ports, lambda unit: unit.alerts.save(Alert(replace(made.record, review=noted))), "OP006"
        )

    def test_an_alert_is_created_new_and_once_per_case_and_loan(self, ports: PortWorld) -> None:
        made = self._stored(ports)
        reviewing = Alert(replace(alert(made.record.loan_id).record, status=AlertStatus.REVIEWING))
        self._refused(ports, lambda unit: unit.alerts.add(reviewing), "OP006")
        twin = alert(made.record.loan_id, made.record.registry_case_id)
        self._refused(ports, lambda unit: unit.alerts.add(twin), "UNIQUE")

    @staticmethod
    def _stored(ports: PortWorld) -> Alert:
        tape = ports.tape(at(1))
        (loan,) = ports.loans(tape.submission_id, vins(1))
        made = alert(loan.loan_id)
        add(ports, made)
        return made

    @staticmethod
    def _save(ports: PortWorld, made: Alert) -> None:
        with ports.units() as unit:
            unit.alerts.save(made)
            unit.commit()

    @staticmethod
    def _refused(ports: PortWorld, write: Callable[[UnitOfWork], object], code: str) -> None:
        with pytest.raises(StoreRefusedError) as raised, ports.units() as unit:
            write(unit)
            unit.commit()
        assert raised.value.refusal == code
