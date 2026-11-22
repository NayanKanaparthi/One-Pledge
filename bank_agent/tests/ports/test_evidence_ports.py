"""The insert-only proof stores of `unit.evidence`: certificates, checkpoints, quarantine, integrity runs."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from bank_testkit.backends import PortWorld
from bank_testkit.memory.bank import vins
from bank_testkit.records import (
    at,
    certificate,
    checkpoint,
    integrity_run,
    planned,
    quarantined,
    request,
)
from onepledge_bank_agent.domain.entities import RegistryRequest
from onepledge_bank_agent.domain.enums import (
    CertificateFailure,
    CheckpointFailure,
    IntegrityRunStatus,
    QuarantineReason,
)
from onepledge_bank_agent.domain.store_errors import StoreRefusedError


def requests(ports: PortWorld, count: int) -> list[RegistryRequest]:
    """`count` committed requests of one item each on a new submission."""
    tape = ports.tape(at(1))
    loans = ports.loans(tape.submission_id, vins(count))
    made = [request(tape.submission_id, index) for index in range(count)]
    with ports.units() as unit:
        for planned_request, loan in zip(made, loans, strict=True):
            unit.requests.add(planned_request, planned([loan.loan_id]))
        unit.commit()
    return made


class TestCertificates:
    """`certificates`: every delivery once, failed ones kept beside the verified one."""

    def test_each_delivery_is_stored_once_and_a_tampered_one_beside_it(self, ports: PortWorld) -> None:
        (made,) = requests(ports, 1)
        genuine = certificate(made.request_id, received=at(3))
        tampered = certificate(
            made.request_id, genuine.certificate_id, failure=CertificateFailure.BODY_HASH_MISMATCH,
            body={"certificate": "altered"}, received=at(2),
        )  # fmt: skip
        resigned = certificate(
            made.request_id, genuine.certificate_id, failure=CertificateFailure.BAD_SIGNATURE,
            signature="b3RoZXI=", received=at(4),
        )  # fmt: skip
        with ports.units() as unit:
            assert unit.evidence.certificates.add(tampered)
            again = replace(tampered, certificate_row_id=uuid.uuid4(), received_at=at(5))
            assert not unit.evidence.certificates.add(again), "the same delivery again"
            assert unit.evidence.certificates.add(genuine), "the same id and signature with the genuine body"
            assert unit.evidence.certificates.add(resigned), "the same id and body with another signature"
            unit.commit()
        with ports.units() as unit:
            assert unit.evidence.certificates.all() == [tampered, genuine, resigned], "oldest first"

    def test_a_second_verified_row_for_a_certificate_or_a_request_is_not_stored(
        self, ports: PortWorld
    ) -> None:
        first, second = requests(ports, 2)
        proven = certificate(first.request_id)
        with ports.units() as unit:
            assert unit.evidence.certificates.add(proven)
            assert not unit.evidence.certificates.add(certificate(second.request_id, proven.certificate_id,
                                                                  signature="b3RoZXI="))  # fmt: skip
            assert not unit.evidence.certificates.add(certificate(first.request_id, signature="dGhpcmQ="))
            unit.commit()
        with ports.units() as unit:
            assert unit.evidence.certificates.all() == [proven]

    def test_verified_certificates_are_found_by_request_and_unproven_ones_are_reported(
        self, ports: PortWorld
    ) -> None:
        first, second, third = requests(ports, 3)
        failed_first = certificate(
            first.request_id, failure=CertificateFailure.CONTENT_MISMATCH, received=at(2)
        )
        proven_first = certificate(
            first.request_id, failed_first.certificate_id, body={"retry": 1}, received=at(3)
        )
        failed_second = certificate(
            second.request_id, failure=CertificateFailure.UNKNOWN_SIGNING_KEY, received=at(2)
        )
        with ports.units() as unit:
            assert not unit.evidence.certificates.any_unproven()
            unit.evidence.certificates.add(failed_first)
            assert unit.evidence.certificates.any_unproven()
            unit.evidence.certificates.add(proven_first)
            assert not unit.evidence.certificates.any_unproven(), "a verified row proves the request"
            unit.evidence.certificates.add(failed_second)
            assert unit.evidence.certificates.any_unproven()
            found = unit.evidence.certificates.verified_for_requests(
                [first.request_id, second.request_id, third.request_id]
            )
            assert unit.evidence.certificates.verified_for_requests([]) == {}
        assert found == {first.request_id: proven_first}


class TestCheckpoints:
    """`checkpoints_seen`: every checkpoint received, failed ones kept."""

    def test_the_latest_verified_checkpoint_ignores_failed_ones(self, ports: PortWorld) -> None:
        second, first = checkpoint(2, received=at(2)), checkpoint(1, received=at(1))
        failed = checkpoint(3, failure=CheckpointFailure.CHAIN_BREAK, head="forged", received=at(3))
        with ports.units() as unit:
            assert unit.evidence.checkpoints.latest_verified() is None
            assert not unit.evidence.checkpoints.any_failed()
            for seen in (second, failed, first):
                unit.evidence.checkpoints.add(seen)
            unit.commit()
        with ports.units() as unit:
            checkpoints = unit.evidence.checkpoints
            assert checkpoints.latest_verified() == second
            assert checkpoints.verified_with_seq(1) == first
            assert checkpoints.verified_with_seq(3) is None
            assert checkpoints.any_failed()
            assert checkpoints.all() == [first, second, failed]

    def test_a_delivery_is_recognised_by_its_seq_head_and_signature(self, ports: PortWorld) -> None:
        seen = checkpoint(4)
        with ports.units() as unit:
            unit.evidence.checkpoints.add(seen)
            unit.commit()
        with ports.units() as unit:
            delivered = unit.evidence.checkpoints.delivered
            assert delivered(4, seen.head_hash.value, seen.signature)
            assert not delivered(5, seen.head_hash.value, seen.signature)
            assert not delivered(4, checkpoint(4, head="other").head_hash.value, seen.signature)
            assert not delivered(4, seen.head_hash.value, "b3RoZXI=")

    def test_checkpoints_of_one_seq_are_listed_in_receipt_order(self, ports: PortWorld) -> None:
        later = checkpoint(6, failure=CheckpointFailure.FORK, head="fork", received=at(9))
        earlier = checkpoint(6, received=at(8))
        with ports.units() as unit:
            unit.evidence.checkpoints.add(later)
            unit.evidence.checkpoints.add(earlier)
            unit.commit()
        with ports.units() as unit:
            assert unit.evidence.checkpoints.all() == [earlier, later]


class TestQuarantine:
    """`registry_messages_quarantined`: each message once, by its message id."""

    def test_a_message_is_held_once_by_its_id_and_checkpoints_are_noticed(self, ports: PortWorld) -> None:
        message_id = uuid.uuid4()
        with ports.units() as unit:
            quarantine = unit.evidence.quarantine
            assert (quarantine.count(), quarantine.any_checkpoint()) == (0, False)
            assert quarantine.add(quarantined(message_id))
            assert not quarantine.add(quarantined(message_id, "CHECKPOINT")), "a repeated delivery"
            assert not quarantine.any_checkpoint()
            assert quarantine.add(quarantined(message_type="CHECKPOINT", reason=QuarantineReason.OVERSIZED))
            unit.commit()
        with ports.units() as unit:
            assert (unit.evidence.quarantine.count(), unit.evidence.quarantine.any_checkpoint()) == (2, True)


class TestIntegrityRuns:
    """`integrity_runs`."""

    def test_the_latest_run_is_the_one_that_ran_last(self, ports: PortWorld) -> None:
        later = integrity_run(at(5), ports.user, IntegrityRunStatus.BROKEN)
        earlier = integrity_run(at(1))
        with ports.units() as unit:
            assert unit.evidence.runs.latest() is None
            unit.evidence.runs.add(later)
            unit.evidence.runs.add(earlier)
            unit.commit()
        with ports.units() as unit:
            assert unit.evidence.runs.latest() == later


class TestEvidenceRefusals:
    """A second write of the same proof is refused or ignored the same way by both stores."""

    def test_the_same_checkpoint_delivery_twice_is_refused(self, ports: PortWorld) -> None:
        seen = checkpoint(8)
        with ports.units() as unit:
            unit.evidence.checkpoints.add(seen)
            unit.commit()
        again = replace(seen, checkpoint_id=uuid.uuid4())
        with pytest.raises(StoreRefusedError) as raised, ports.units() as unit:
            unit.evidence.checkpoints.add(again)
        assert raised.value.refusal == "UNIQUE"
        other_head = checkpoint(8, head="other")
        with pytest.raises(StoreRefusedError) as second, ports.units() as unit:
            unit.evidence.checkpoints.add(other_head)
        assert second.value.refusal == "UNIQUE", "one verified checkpoint per seq"
        with ports.units() as unit:
            assert unit.evidence.checkpoints.all() == [seen]
