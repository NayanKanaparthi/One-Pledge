"""`OutboxLedgerRepository` and `OutboxStateRepository` (SPEC_DECISIONS 101), the same in both stores."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from bank_testkit.backends import PortWorld
from bank_testkit.parity import parity
from bank_testkit.records import at
from onepledge_bank_agent.domain.outbox import AckStatus, LedgerEntry, OutboxOutcome, OutboxState
from onepledge_bank_agent.domain.store_errors import StoreRefusedError


class TestOutboxLedger:
    """`registry_outbox_messages`: one row per message, its outcome set once."""

    @parity("OutboxLedgerRepository.get", "OutboxLedgerRepository.save")
    def test_an_entry_reads_back_after_each_step_and_an_unseen_message_has_none(
        self, ports: PortWorld
    ) -> None:
        seen = LedgerEntry.first_seen(uuid.uuid4(), "CASE_UPDATE", at(1))
        steps = [
            seen,
            seen.deferred("STORE_UNAVAILABLE", at(2)),
            seen.deferred("STORE_UNAVAILABLE", at(2)).handled_as(OutboxOutcome.QUARANTINED, at(3)),
        ]
        steps += [
            steps[-1].acknowledged(AckStatus.ACKED, at(4)),
            steps[-1].acknowledged(AckStatus.ACKED, at(4)).handled_as(OutboxOutcome.APPLIED_LATE, at(5)),
        ]
        for step in steps:
            with ports.units() as unit:
                unit.outbox_ledger.save(step)
                unit.commit()
            with ports.units() as unit:
                assert unit.outbox_ledger.get(seen.message_id) == step
        with ports.units() as unit:
            assert unit.outbox_ledger.get(uuid.uuid4()) is None

    @parity("OutboxLedgerRepository.save")
    def test_a_second_outcome_is_refused(self, ports: PortWorld) -> None:
        applied = LedgerEntry.first_seen(uuid.uuid4(), "ALERT", at(1)).handled_as(
            OutboxOutcome.APPLIED, at(2)
        )
        with ports.units() as unit:
            unit.outbox_ledger.save(applied)
            unit.commit()
        changed = LedgerEntry(
            applied.message_id, applied.message_type, applied.first_seen_at, applied.handled_at,
            OutboxOutcome.IGNORED, applied.ack_status, None, at(3),
        )  # fmt: skip
        with pytest.raises(StoreRefusedError) as refused, ports.units() as unit:
            unit.outbox_ledger.save(changed)
        assert refused.value.sqlstate == "OP009"

    @parity("OutboxLedgerRepository.applied_late")
    def test_only_case_updates_applied_late_are_listed(self, ports: PortWorld) -> None:
        late = LedgerEntry.first_seen(uuid.uuid4(), "CASE_UPDATE", at(1)).handled_as(
            OutboxOutcome.QUARANTINED, at(2)
        )
        held = LedgerEntry.first_seen(uuid.uuid4(), "CASE_UPDATE", at(1)).handled_as(
            OutboxOutcome.QUARANTINED, at(2)
        )
        with ports.units() as unit:
            assert unit.outbox_ledger.applied_late() == set()
            unit.outbox_ledger.save(late)
            unit.outbox_ledger.save(held)
            unit.commit()
        with ports.units() as unit:
            unit.outbox_ledger.save(late.handled_as(OutboxOutcome.APPLIED_LATE, at(3)))
            unit.commit()
        with ports.units() as unit:
            assert unit.outbox_ledger.applied_late() == {late.message_id}


class TestOutboxState:
    """`registry_outbox_state`: the single row of completeness and blindness."""

    @parity("OutboxStateRepository.get", "OutboxStateRepository.save")
    def test_the_state_starts_never_complete_and_reads_back_as_replaced(self, ports: PortWorld) -> None:
        with ports.units() as unit:
            assert unit.outbox_state.get() == OutboxState()
        limit = timedelta(minutes=5)
        complete = OutboxState().after_sync(at(1), True, None, limit)[0]
        blind = complete.after_sync(at(20), False, "NO_PROGRESS", limit)[0]
        for state in (complete, blind, OutboxState(at(30))):
            with ports.units() as unit:
                unit.outbox_state.save(state)
                unit.commit()
            with ports.units() as unit:
                assert unit.outbox_state.get() == state
