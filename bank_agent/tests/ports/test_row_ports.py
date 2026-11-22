"""`RawRowRepository` and `SubmissionItemRepository`: the verbatim rows and the clean stage's items."""

from __future__ import annotations

import uuid
from dataclasses import replace

from bank_testkit.backends import PortWorld
from bank_testkit.memory.bank import vins
from bank_testkit.records import at, item, raw_rows
from bank_testkit.tapes import SUMMIT_HEADER
from onepledge_bank_agent.application.ports import AcceptedItem, NewItem, StoredRawRow
from onepledge_bank_agent.domain.enums import CleaningAction, CleaningProblem, RowDisposition
from onepledge_bank_agent.domain.values import CleaningIssue


def stored_rows(ports: PortWorld, submission_id: uuid.UUID, count: int) -> list[StoredRawRow]:
    """Store `count` raw rows, written in reverse row order, and read them back."""
    rows = raw_rows(SUMMIT_HEADER, count)
    with ports.units() as unit:
        unit.raw_rows.add_many(submission_id, list(reversed(rows)), at(1))
        unit.commit()
    with ports.units() as unit:
        return unit.raw_rows.list_for(submission_id)


class TestRawRows:
    """`loans_raw`."""

    def test_rows_read_back_verbatim_in_row_number_order(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        rows = raw_rows(SUMMIT_HEADER, 3)
        found = stored_rows(ports, tape.submission_id, 3)
        assert [(r.row_number, r.text, r.cells, r.row_sha256) for r in found] == [
            (r.row_number, r.text, r.cells, r.row_sha256) for r in rows
        ]
        assert len({r.raw_id for r in found}) == 3

    def test_rows_belong_to_their_submission_and_an_empty_add_stores_nothing(self, ports: PortWorld) -> None:
        tape, other = ports.tape(at(1)), ports.tape(at(2))
        stored_rows(ports, tape.submission_id, 2)
        with ports.units() as unit:
            unit.raw_rows.add_many(other.submission_id, [], at(3))
            unit.commit()
        with ports.units() as unit:
            assert unit.raw_rows.list_for(other.submission_id) == []
            assert len(unit.raw_rows.list_for(tape.submission_id)) == 2


class TestSubmissionItems:
    """`submission_items` and `cleaning_issues`."""

    def test_accepted_and_release_items_are_read_by_disposition_in_row_order(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        rows = stored_rows(ports, tape.submission_id, 6)
        loans = ports.loans(tape.submission_id, vins(5))
        dispositions = [
            RowDisposition.ACCEPTED_REPRESENTED, RowDisposition.ALREADY_LOCKED, RowDisposition.ACCEPTED_NEW,
            RowDisposition.RELEASE_REQUESTED, RowDisposition.RELEASE_REQUESTED,
        ]  # fmt: skip
        items = [
            item(row.raw_id, row.row_number, d, loan.loan_id)
            for row, d, loan in zip(rows[:5], dispositions, loans, strict=True)
        ]
        issue = CleaningIssue("vin", CleaningProblem.WHITESPACE_TRIMMED, CleaningAction.FIXED)
        rejected = replace(
            item(rows[5].raw_id, rows[5].row_number, RowDisposition.REJECTED, None), issues=(issue,)
        )
        with ports.units() as unit:
            unit.items.add_many(tape.submission_id, [*reversed(items), rejected])
            unit.commit()
        with ports.units() as unit:
            assert unit.items.accepted(tape.submission_id) == [
                AcceptedItem(1, loans[0].loan_id), AcceptedItem(3, loans[2].loan_id),
            ]  # fmt: skip
            assert unit.items.release_requested(tape.submission_id) == [
                AcceptedItem(4, loans[3].loan_id), AcceptedItem(5, loans[4].loan_id),
            ]  # fmt: skip
            assert unit.items.accepted(ports.tape_of().submission_id) == []

    def test_the_latest_submission_of_a_loan_is_the_one_uploaded_last(self, ports: PortWorld) -> None:
        first, later = ports.tape(at(1)), ports.tape(at(3))
        ports.tape(at(5))
        loan = ports.loans(first.submission_id, vins(1))[0]
        for tape in (later, first):
            row = stored_rows(ports, tape.submission_id, 1)[0]
            with ports.units() as unit:
                unit.items.add_many(
                    tape.submission_id, [item(row.raw_id, 1, RowDisposition.ACCEPTED_NEW, loan.loan_id)]
                )
                unit.commit()
        with ports.units() as unit:
            assert unit.items.latest_submission_of(loan.loan_id) == later.submission_id
            assert unit.items.latest_submission_of(uuid.uuid4()) is None

    def test_an_empty_add_stores_nothing(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        empty: list[NewItem] = []
        with ports.units() as unit:
            unit.items.add_many(tape.submission_id, empty)
            unit.commit()
        with ports.units() as unit:
            assert unit.items.accepted(tape.submission_id) == []
            assert unit.items.release_requested(tape.submission_id) == []
