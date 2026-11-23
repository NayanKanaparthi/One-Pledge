"""The insert-only proof stores of `unit.evidence`: certificates, checkpoints, quarantine, integrity runs."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace

import httpx
import pytest

from bank_testkit.backends import PortWorld
from bank_testkit.longest import Longest
from bank_testkit.memory.bank import vins
from bank_testkit.parity import parity
from bank_testkit.records import (
    answer,
    at,
    certificate,
    checkpoint,
    integrity_run,
    outcome,
    planned,
    quarantined,
    request,
)
from bank_testkit.scripted_http import registry
from onepledge_bank_agent.domain.canonical_json import JsonValue, canonical_json
from onepledge_bank_agent.domain.certificate_rules import delivered_sha256
from onepledge_bank_agent.domain.entities import RegistryRequest
from onepledge_bank_agent.domain.enums import (
    CertificateFailure,
    CheckpointFailure,
    IntegrityRunStatus,
    QuarantineReason,
    RegistryItemResult,
)
from onepledge_bank_agent.domain.registry_records import QuarantinedMessage
from onepledge_bank_agent.domain.store_errors import StoreRefusedError
from onepledge_bank_agent.domain.tape import TapeSubmission
from onepledge_bank_agent.domain.values import Sha256Hex
from onepledge_bank_agent.infrastructure.settings import DEFAULT_RESPONSE_BYTES


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


def complete(ports: PortWorld, submission_id: uuid.UUID) -> None:
    """Move a stored submission forward to COMPLETED, one legal step at a time."""
    moves = (
        TapeSubmission.mark_cleaned, TapeSubmission.mark_checked, TapeSubmission.mark_tokenized,
        TapeSubmission.mark_submitted, TapeSubmission.mark_completed,
    )  # fmt: skip
    for move in moves:
        with ports.units() as unit:
            tape = unit.tapes.get(submission_id, for_update=True)
            assert tape is not None
            move(tape, at(9))
            unit.tapes.save(tape)
            unit.commit()


class TestCertificates:
    """`certificates`: every delivery once, failed ones kept beside the verified one."""

    @parity("CertificateRepository.add", "CertificateRepository.all")
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

    @parity("CertificateRepository.add", "CertificateRepository.all")
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

    @parity(
        "CertificateRepository.add",
        "CertificateRepository.any_unproven",
        "CertificateRepository.verified_for_requests",
    )
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

    @parity(
        "CertificateRepository.add",
        "CertificateRepository.missing_for_completed",
        "RegistryRequestRepository.items",
        "RegistryRequestRepository.mark_sent",
        "RegistryRequestRepository.store_answer",
    )
    def test_an_answered_request_of_a_completed_tape_without_its_verified_row_is_missing(
        self, ports: PortWorld
    ) -> None:
        """CERTIFICATE_MISSING (SPEC_DECISIONS 89 a): only COMPLETED tapes, only answered requests."""
        proven, failed, unanswered = requests(ports, 3)
        answers = {}
        with ports.units() as unit:
            for made in (proven, failed):
                items = unit.requests.items(made.request_id)
                answers[made.request_id] = answer(
                    made, [outcome(items[0].item_ref, RegistryItemResult.LOCKED)[0]]
                )
                unit.requests.mark_sent(made.request_id, at(2))
                unit.requests.store_answer(made.request_id, answers[made.request_id], at(3))
            unit.evidence.certificates.add(
                certificate(proven.request_id, answers[proven.request_id].certificate_id)
            )
            unit.evidence.certificates.add(certificate(
                failed.request_id, answers[failed.request_id].certificate_id,
                failure=CertificateFailure.BAD_SIGNATURE,
            ))  # fmt: skip
            unit.commit()
        with ports.units() as unit:
            assert unit.evidence.certificates.missing_for_completed() == [], "the tape is not COMPLETED"
        complete(ports, proven.submission_id)
        with ports.units() as unit:
            missing = unit.evidence.certificates.missing_for_completed()
        assert missing == [(failed.request_id, answers[failed.request_id].certificate_id)]
        assert unanswered.request_id not in {request_id for request_id, _ in missing}


class TestCheckpoints:
    """`checkpoints_seen`: every checkpoint received, failed ones kept."""

    @parity(
        "CheckpointRepository.add",
        "CheckpointRepository.all",
        "CheckpointRepository.any_failed",
        "CheckpointRepository.latest_verified",
        "CheckpointRepository.verified_with_seq",
    )
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

    @parity("CheckpointRepository.add", "CheckpointRepository.delivered")
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

    @parity("CheckpointRepository.add", "CheckpointRepository.all")
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

    @parity("QuarantineRepository.add", "QuarantineRepository.any_checkpoint", "QuarantineRepository.count")
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

    @parity("QuarantineRepository.add", "QuarantineRepository.all")
    def test_every_held_message_is_listed_oldest_first(self, ports: PortWorld) -> None:
        later, earlier = quarantined(received=at(5)), quarantined(received=at(1))
        with ports.units() as unit:
            assert unit.evidence.quarantine.all() == []
            unit.evidence.quarantine.add(later)
            unit.evidence.quarantine.add(earlier)
            unit.commit()
        with ports.units() as unit:
            assert unit.evidence.quarantine.all() == [earlier, later]

    @parity("QuarantineRepository.add", "QuarantineRepository.all")
    def test_a_withheld_payload_is_kept_as_its_hash_and_told_apart_from_a_json_null(
        self, ports: PortWorld
    ) -> None:
        """Migration 0011: a payload the bank cannot store is withheld like an oversized one; a payload the
        registry sent as JSON null is kept as such."""
        base = quarantined(received=at(1))
        withheld = replace(base, quarantined=replace(base.quarantined, payload=None, withheld=True))
        sent_null = replace(quarantined(received=at(2)), quarantined=replace(base.quarantined, payload=None))
        with ports.units() as unit:
            assert unit.evidence.quarantine.add(withheld)
            assert unit.evidence.quarantine.add(sent_null)
            unit.commit()
        with ports.units() as unit:
            assert unit.evidence.quarantine.all() == [withheld, sent_null]

    @parity("QuarantineRepository.add")
    def test_an_oversized_payload_that_is_not_withheld_is_refused(self, ports: PortWorld) -> None:
        oversized = quarantined(reason=QuarantineReason.OVERSIZED)
        held = replace(oversized.quarantined, payload={"x": 1}, withheld=False)
        kept = replace(oversized, quarantined=held)
        with pytest.raises(StoreRefusedError) as raised, ports.units() as unit:
            unit.evidence.quarantine.add(kept)
        assert raised.value.sqlstate == "23514"


class TestUnmatchedCaseUpdates:
    """SPEC_DECISIONS 101: a case update held until its alert arrives, found by case and asset code."""

    @parity("QuarantineRepository.add", "QuarantineRepository.unmatched_case_updates")
    def test_only_unmatched_case_updates_of_that_case_and_code_are_found_newest_first(
        self, ports: PortWorld
    ) -> None:
        case_id, code = uuid.uuid4(), "c" * 64

        def held(
            received: int,
            case: uuid.UUID = case_id,
            token: str = code,
            *,
            kind: str = "CASE_UPDATE",
            reason: QuarantineReason = QuarantineReason.UNMATCHED,
        ) -> QuarantinedMessage:
            made = quarantined(message_type=kind, reason=reason, received=at(received))
            payload: JsonValue = {
                "case_id": str(case), "asset_token": token, "status": "CONFIRMED", "resolved_seq": 9,
            }  # fmt: skip
            return replace(made, quarantined=replace(made.quarantined, payload=payload))

        older, newer = held(1), held(2)
        others = [
            held(3, uuid.uuid4()), held(4, token="d" * 64), held(5, kind="ALERT"),
            held(6, reason=QuarantineReason.SCHEMA_MISMATCH),
        ]  # fmt: skip
        with ports.units() as unit:
            assert unit.evidence.quarantine.unmatched_case_updates(case_id, code) == []
            for message in (older, *others, newer):
                unit.evidence.quarantine.add(message)
            unit.commit()
        with ports.units() as unit:
            assert unit.evidence.quarantine.unmatched_case_updates(case_id, code) == [newer, older]


class TestIntegrityRuns:
    """`integrity_runs`."""

    @parity("IntegrityRunRepository.add", "IntegrityRunRepository.ids", "IntegrityRunRepository.latest")
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
            assert unit.evidence.runs.ids() == {later.run_id, earlier.run_id}


class TestEvidenceRefusals:
    """A second write of the same proof is refused or ignored the same way by both stores."""

    @parity("CheckpointRepository.add", "CheckpointRepository.all")
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


class TestContractBoundedCertificate:
    """SPEC_DECISIONS 125: a certificate body is contract-bounded and stored whole."""

    @parity("CertificateRepository.add", "CertificateRepository.all")
    def test_a_certificate_of_500_items_in_the_longest_legal_form_is_stored_and_read_back(
        self, ports: PortWorld
    ) -> None:
        longest = Longest("registry.yaml")
        signed = longest.of("SignedCertificate")
        assert list(longest.validator("SignedCertificate").iter_errors(signed)) == []
        size = len(canonical_json(signed["body"]))
        assert len(signed["body"]["items"]) == 500 and size <= DEFAULT_RESPONSE_BYTES
        raw = json.dumps(signed).encode()
        read = registry(lambda _: httpx.Response(200, content=raw)).certificate(uuid.uuid4())
        assert read.body == signed["body"]
        (made,) = requests(ports, 1)
        delivered = delivered_sha256(read.body)
        assert delivered is not None
        kept = replace(
            certificate(made.request_id), body=read.body, issued_at=read.issued_at,
            signature=read.signature, signing_key_id=read.signing_key_id,
            failure=CertificateFailure.BAD_SIGNATURE, delivered_body_sha256=Sha256Hex(delivered),
        )  # fmt: skip
        with ports.units() as unit:
            assert unit.evidence.certificates.add(kept)
            unit.commit()
        with ports.units() as unit:
            assert unit.evidence.certificates.all() == [kept]
