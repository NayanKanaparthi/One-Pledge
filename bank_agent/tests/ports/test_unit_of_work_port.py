"""`UnitOfWork`: one transaction; nothing written in a scope left without commit is visible afterwards."""

from __future__ import annotations

import uuid
from itertools import pairwise

import pytest

from bank_testkit.backends import PortWorld
from bank_testkit.memory.bank import vins
from bank_testkit.parity import parity
from bank_testkit.records import at
from bank_testkit.tapes import SUMMIT_LAYOUT
from onepledge_bank_agent.domain.audit import Actor, AuditDraft, first_broken_seq
from onepledge_bank_agent.domain.enums import AuditAction, AuditObjectType, Role


def draft(action: AuditAction = AuditAction.TAPE_STAGE_ADVANCED, number: int = 1) -> AuditDraft:
    """An audit draft naming its number in the detail."""
    return AuditDraft(
        action, Actor.system(), AuditObjectType.TAPE_SUBMISSION, uuid.uuid4(), detail={"n": number}
    )


class FailedScopeError(Exception):
    """Raised inside a unit of work to leave it by an exception."""


class TestUnitOfWork:
    """Commit, rollback and the hash chained audit append."""

    @parity("TapeRepository.add", "TapeRepository.get", "UnitOfWork.commit")
    def test_a_committed_write_is_visible_to_the_next_unit(self, ports: PortWorld) -> None:
        tape = ports.tape_of(at(1))
        with ports.units() as unit:
            unit.tapes.add(tape, SUMMIT_LAYOUT)
            assert unit.tapes.get(tape.submission_id) is not None, "a unit reads its own writes"
            unit.commit()
        with ports.units() as unit:
            stored = unit.tapes.get(tape.submission_id)
        assert stored is not None and stored.facts == tape.facts

    @parity(
        "HistoryReader.audit_size",
        "HistoryReader.submission_ids",
        "LoanRepository.get_many",
        "LoanRepository.save",
        "TapeRepository.add",
        "UnitOfWork.record",
    )
    def test_leaving_without_commit_discards_every_write_and_every_queued_audit_entry(
        self, ports: PortWorld
    ) -> None:
        tape = ports.tape(at(1))
        loans_before = ports.loans(tape.submission_id, vins(1))
        with ports.units() as unit:
            before = unit.history.audit_size()
        with ports.units() as unit:
            unit.tapes.add(ports.tape_of(at(2)), SUMMIT_LAYOUT)
            loan = unit.loans.get_many([loans_before[0].loan_id])[loans_before[0].loan_id]
            loan.hold_local(at(3))
            unit.loans.save(loan)
            unit.record(draft())
        with ports.units() as unit:
            assert unit.history.submission_ids() == [tape.submission_id]
            stored = unit.loans.get_many([loan.loan_id])[loan.loan_id]
            assert stored.record == loans_before[0].record
            assert unit.history.audit_size() == before

    @parity("HistoryReader.submission_ids", "TapeRepository.add", "TapeRepository.get", "UnitOfWork.record")
    def test_an_exception_inside_the_scope_rolls_back(self, ports: PortWorld) -> None:
        tape = ports.tape_of(at(1))
        with pytest.raises(FailedScopeError), ports.units() as unit:
            unit.tapes.add(tape, SUMMIT_LAYOUT)
            unit.record(draft())
            raise FailedScopeError
        with ports.units() as unit:
            assert unit.tapes.get(tape.submission_id) is None
            assert unit.history.submission_ids() == []

    @parity(
        "HistoryReader.audit_entries", "HistoryReader.audit_size", "UnitOfWork.commit", "UnitOfWork.record"
    )
    def test_committed_audit_entries_are_chained_in_order_without_gaps(self, ports: PortWorld) -> None:
        with ports.units() as unit:
            count, highest = unit.history.audit_size()
        assert count == highest, "seq starts at 1 and has no gaps"
        user = ports.user
        for batch in (1, 3, 2):
            with ports.units() as unit:
                for number in range(batch):
                    unit.record(draft(number=number))
                unit.commit()
        with ports.units() as unit:
            unit.record(
                AuditDraft(AuditAction.TAPE_RETRY_REQUESTED, Actor.user(user, Role.CREDIT_OFFICER),
                           AuditObjectType.TAPE_SUBMISSION, None, detail={"n": 99})
            )  # fmt: skip
            unit.commit()
        with ports.units() as unit:
            entries = list(unit.history.audit_entries())
            assert unit.history.audit_size() == (count + 7, highest + 7)
        added = entries[count:]
        assert [e.seq for e in added] == list(range(highest + 1, highest + 8))
        assert [e.detail for e in added] == [{"n": n} for n in (0, 0, 1, 2, 0, 1, 99)]
        assert added[-1].action == AuditAction.TAPE_RETRY_REQUESTED.value
        assert added[-1].actor_user_id == user
        assert all(later.prev_hash == earlier.entry_hash for earlier, later in pairwise(entries))
        assert first_broken_seq(entries) is None
