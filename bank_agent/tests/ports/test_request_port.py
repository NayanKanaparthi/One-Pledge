"""`RegistryRequestRepository`: planned requests, their attempts, answers and per item results."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace

import httpx

from bank_testkit.backends import PortWorld
from bank_testkit.longest import Longest
from bank_testkit.memory.bank import vins
from bank_testkit.parity import parity
from bank_testkit.records import answer, at, outcome, planned, request
from bank_testkit.scripted_http import registry
from onepledge_bank_agent.application.evidence_ports import ItemRecord, PlannedItem
from onepledge_bank_agent.domain.canonical_json import canonical_json
from onepledge_bank_agent.domain.entities import RegistryRequest
from onepledge_bank_agent.domain.enums import RegistryItemResult, RequestOrigin, TapeKind, TokenKind
from onepledge_bank_agent.domain.registry_records import RegistryItem, RegistrySubmission
from onepledge_bank_agent.domain.stage_digests import body_sha256
from onepledge_bank_agent.domain.storable import FREE_FORM_BYTES
from onepledge_bank_agent.infrastructure.settings import DEFAULT_RESPONSE_BYTES

SUBMISSION = RegistrySubmission(
    TapeKind.PLEDGE, "1.1", (RegistryItem(uuid.uuid4(), "a" * 64, {TokenKind.VIN: "b" * 64}),)
)


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

    @parity(
        "RegistryRequestRepository.by_key", "RegistryRequestRepository.get", "RegistryRequestRepository.items"
    )
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

    @parity("RegistryRequestRepository.add", "RegistryRequestRepository.for_submission")
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

    @parity("RegistryRequestRepository.get", "RegistryRequestRepository.mark_sent")
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

    @parity(
        "CertificateRepository.expected",
        "RegistryRequestRepository.get",
        "RegistryRequestRepository.items",
        "RegistryRequestRepository.mark_sent",
        "RegistryRequestRepository.store_answer",
        "RegistryRequestRepository.store_result",
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


class TestContractBoundedAnswer:
    """SPEC_DECISIONS 125: a contract-bounded document is stored whole up to `HTTP_MAX_RESPONSE_BYTES`."""

    @parity(
        "HistoryReader.request_rows",
        "RegistryRequestRepository.get",
        "RegistryRequestRepository.store_answer",
    )
    def test_an_answer_of_500_results_in_the_longest_legal_form_is_stored_and_read_back(
        self, ports: PortWorld
    ) -> None:
        longest = Longest("registry.yaml")
        body = longest.of("SubmissionResult")
        assert list(longest.validator("SubmissionResult").iter_errors(body)) == []
        size = len(canonical_json(body))
        assert len(body["results"]) == 500 and FREE_FORM_BYTES < size <= DEFAULT_RESPONSE_BYTES
        raw = json.dumps(body).encode()
        read = registry(lambda _: httpx.Response(201, content=raw)).submit("f" * 64, SUBMISSION, lambda: None)
        assert read.body == body and len(read.results) == 500
        made, _ = stored(ports, 1)
        with ports.units() as unit:
            unit.requests.store_answer(made.request_id, read, at(2))
            unit.commit()
        with ports.units() as unit:
            (row,) = unit.history.request_rows([made.request_id])
            stored_request = unit.requests.get(made.request_id)
        assert body_sha256(body) in row, "the body reads back exactly: its canonical hash is the same"
        assert stored_request is not None and stored_request.as_of_seq == body["as_of_seq"]
