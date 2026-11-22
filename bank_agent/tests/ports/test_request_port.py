"""`RegistryRequestRepository`: planned requests, their attempts, answers and per item results."""

from __future__ import annotations

import uuid
from dataclasses import replace

from bank_testkit.backends import PortWorld
from bank_testkit.memory.bank import vins
from bank_testkit.records import answer, at, outcome, planned, request
from onepledge_bank_agent.application.evidence_ports import ItemRecord, PlannedItem
from onepledge_bank_agent.domain.entities import RegistryRequest
from onepledge_bank_agent.domain.enums import RegistryItemResult, RequestOrigin


def stored(ports: PortWorld, count: int = 2) -> tuple[RegistryRequest, list[PlannedItem]]:
    """A committed request of `count` items on a new submission."""
    tape = ports.tape(at(1))
    loans = ports.loans(tape.submission_id, vins(count))
    made = request(tape.submission_id, item_count=count)
    items = planned([loan.loan_id for loan in loans])
    with ports.units() as unit:
        unit.requests.add(made, list(reversed(items)))
        unit.commit()
    return made, items


class TestRequests:
    """`registry_requests` and `registry_request_items`."""

    def test_a_request_reads_back_by_id_and_by_key_with_its_items_in_position_order(
        self, ports: PortWorld
    ) -> None:
        made, items = stored(ports)
        with ports.units() as unit:
            assert unit.requests.get(made.request_id) == made
            assert unit.requests.by_key(made.idempotency_key.value) == made
            assert unit.requests.get(uuid.uuid4()) is None
            assert unit.requests.by_key("0" * 64) is None
            assert unit.requests.items(made.request_id) == [
                ItemRecord(i.item_ref, i.position, i.loan_id, None, None) for i in items
            ]
            assert unit.requests.items(uuid.uuid4()) == []

    def test_a_submissions_requests_are_read_by_origin_in_batch_order(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        loans = ports.loans(tape.submission_id, vins(3))
        second, first = request(tape.submission_id, 1), request(tape.submission_id, 0)
        resubmit = request(tape.submission_id, 0, origin=RequestOrigin.RESUBMIT)
        with ports.units() as unit:
            for made, loan in zip((second, first, resubmit), loans, strict=True):
                unit.requests.add(made, planned([loan.loan_id]))
            unit.commit()
        with ports.units() as unit:
            assert unit.requests.for_submission(tape.submission_id, RequestOrigin.TAPE) == [first, second]
            assert unit.requests.for_submission(tape.submission_id, RequestOrigin.RESUBMIT) == [resubmit]
            assert unit.requests.for_submission(uuid.uuid4(), RequestOrigin.TAPE) == []

    def test_each_send_counts_one_more_attempt(self, ports: PortWorld) -> None:
        made, _ = stored(ports)
        with ports.units() as unit:
            assert unit.requests.mark_sent(made.request_id, at(2)) == 1
            assert unit.requests.mark_sent(made.request_id, at(3)) == 2
            unit.commit()
        with ports.units() as unit:
            assert unit.requests.mark_sent(made.request_id, at(4)) == 3
        with ports.units() as unit:
            assert unit.requests.get(made.request_id) == replace(made, attempts=2), (
                "the third was rolled back"
            )

    def test_an_answer_and_each_items_result_are_stored(self, ports: PortWorld) -> None:
        made, items = stored(ports)
        case = uuid.uuid4()
        locked, _ = outcome(items[0].item_ref, RegistryItemResult.LOCKED)
        conflict, conflict_result = outcome(items[1].item_ref, RegistryItemResult.CONFLICT, case)
        given = answer(made, [locked, conflict])
        with ports.units() as unit:
            unit.requests.mark_sent(made.request_id, at(2))
            unit.requests.store_answer(made.request_id, given, at(3))
            unit.requests.store_result(made.request_id, conflict, conflict_result)
            unit.commit()
        with ports.units() as unit:
            found = unit.requests.get(made.request_id)
            stored_items = unit.requests.items(made.request_id)
            expected = unit.evidence.certificates.expected()
        assert found == replace(
            made, attempts=1, response_status=201, certificate_id=given.certificate_id,
            registry_submission_id=given.submission_id, as_of_seq=given.as_of_seq,
        )  # fmt: skip
        assert stored_items == [
            ItemRecord(items[0].item_ref, 0, items[0].loan_id, None, None),
            ItemRecord(items[1].item_ref, 1, items[1].loan_id, RegistryItemResult.CONFLICT, case),
        ]
        assert expected == 1, "an answered request names the certificate it should have"
