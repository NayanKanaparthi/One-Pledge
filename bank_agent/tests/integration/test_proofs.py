"""Tampered certificates and checkpoints through the whole stack (SPEC 15.2, 15.3, 15.4, 21.5).

Every reason of 15.2 and 15.3 is produced against the real registry double: by its
test controls (CERTIFICATE_BODY, BAD_CHECKPOINT_SIGNATURE, EDIT_IN_PLACE), by a bank
misconfigured against it (an unknown key id, a wrong public key, another member's id),
or by a compromised registry that signs a forked or regressing checkpoint with its own
key. Each failure is stored, audited, kept in the integrity report, and the halt rule
stops the next submission until an operator disables it (audited at startup). A tape
whose certificate failed is retryable (SPEC_DECISIONS 61): the failed row stays as
evidence and a retry fetches the certificate again.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from psycopg.types.json import Jsonb

from bank_testkit.data import seed_reference
from bank_testkit.database import BankDatabase, pairs
from bank_testkit.doubles import REGISTRY_URL, SIGNING_KEY_ID, InProcessDoubles
from bank_testkit.outside import InProcessOutsideRecords
from bank_testkit.pipeline import BankAgentHarness, Services
from bank_testkit.world import Faults
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.domain.canonical_json import canonical_sha256
from onepledge_bank_agent.domain.enums import (
    CertificateFailure,
    CheckpointFailure,
    IntegrityRunStatus,
    TapeKind,
)
from onepledge_bank_agent.domain.integrity import CertificateFinding, SectionStatus
from onepledge_bank_agent.domain.tape import TapeSubmission

pytestmark = pytest.mark.integration

TAPE = "tapes/HARBOR/S06_HARBOR-LAKESIDE_PLEDGE.csv"
FACILITY, KIND, AS_OF = "HARBOR-LAKESIDE", TapeKind.PLEDGE, date(2026, 10, 15)
SECOND = "tapes/HARBOR/S07_HARBOR-SUMMIT_PLEDGE.csv"


def other_public_key() -> str:
    raw = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


@dataclass
class Bank:
    database: BankDatabase
    doubles: InProcessDoubles
    data: Path
    outside: InProcessOutsideRecords

    def agent(self, faults: Faults | None = None, **variables: str) -> BankAgentHarness:
        return BankAgentHarness(
            self.database, Services(self.outside, self.doubles), faults=faults, environment=variables
        )

    def upload(self, agent: BankAgentHarness, tape: str = TAPE) -> TapeSubmission:
        if tape == TAPE:
            return agent.ingest(self.data / tape, FACILITY, KIND, AS_OF)
        return agent.ingest(self.data / tape, "HARBOR-SUMMIT", KIND, AS_OF)

    def one(self, query: str, parameters: tuple[object, ...] = ()) -> tuple[object, ...]:
        with self.database.connect("admin") as connection:
            row = connection.execute(query, parameters).fetchone()
        assert row is not None
        return tuple(row)

    def count(self, query: str) -> int:
        (value,) = self.one(query)
        assert isinstance(value, int)
        return value

    def tape(self, submission: TapeSubmission) -> tuple[object, ...]:
        return self.one(
            "SELECT status, failure_code FROM tape_submissions WHERE submission_id = %s",
            (submission.submission_id,),
        )


@pytest.fixture
def bank(
    bank_db: BankDatabase, small_data: Path, small_outside: InProcessOutsideRecords, doubles: InProcessDoubles
) -> Iterator[Bank]:
    seed_reference(bank_db, small_data)
    yield Bank(bank_db, doubles, small_data, small_outside)


def assert_certificate_invalid(
    bank: Bank, agent: BankAgentHarness, tape: TapeSubmission, reason: str
) -> None:
    """FAILED with CERTIFICATE_INVALID, loan states kept, the reason stored and reported; a retry
    fetches the same bad certificate again, stores nothing new and fails the same way."""
    assert bank.tape(tape) == ("FAILED", "CERTIFICATE_INVALID")
    assert bank.one("SELECT count(*) FROM loans WHERE state = 'PENDING'") == (0,)
    locked = bank.one("SELECT count(*) FROM loans WHERE state = 'LOCKED'")
    assert locked == (len(bank.doubles.active_locks("HARBOR")),)
    stored = bank.one("SELECT verified, failure_reason FROM certificates ORDER BY received_at LIMIT 1")
    assert stored == (False, reason)
    assert bank.count("SELECT count(*) FROM audit_log WHERE action = 'CERTIFICATE_REJECTED'") >= 1
    report = agent.report()
    assert report.status is IntegrityRunStatus.BROKEN and report.certificates.status is SectionStatus.BROKEN
    assert reason in {f.failure_reason.value for f in report.certificates.failures}
    assert report.certificates.verified < report.certificates.total
    rows, rejected = certificate_rows(bank), rejected_entries(bank)
    fetched = certificate_fetches(bank)
    agent.retry(tape)
    agent.run_worker()
    assert bank.tape(tape) == ("FAILED", "CERTIFICATE_INVALID")
    assert certificate_fetches(bank) > fetched, "the retry fetched the certificate again"
    assert (certificate_rows(bank), rejected_entries(bank)) == (rows, rejected), "nothing new stored"


def certificate_rows(bank: Bank) -> int:
    return bank.count("SELECT count(*) FROM certificates")


def rejected_entries(bank: Bank) -> int:
    return bank.count("SELECT count(*) FROM audit_log WHERE action = 'CERTIFICATE_REJECTED'")


def certificate_fetches(bank: Bank) -> int:
    return sum(1 for sent in bank.doubles.sent if sent.path.startswith("/v1/certificates/"))


def test_a_certificate_and_checkpoints_under_an_unknown_signing_key_are_refused(bank: Bank) -> None:
    keys = json.dumps({"registry-other-1": bank.doubles.public_key_b64})
    with bank.agent(REGISTRY_PUBLIC_KEYS=keys, HALT_ON_REGISTRY_INTEGRITY_FAILURE="false") as agent:
        tape = bank.upload(agent)
        agent.run_worker()
        assert_certificate_invalid(bank, agent, tape, CertificateFailure.UNKNOWN_SIGNING_KEY.value)
        agent.sync()
        failed = "SELECT failure_reason, 'stored' FROM checkpoints_seen GROUP BY 1"
        assert pairs(bank.database, failed) == {CheckpointFailure.UNKNOWN_SIGNING_KEY.value: "stored"}
        assert agent.report().registry_chain.status is SectionStatus.BROKEN


def test_a_certificate_and_checkpoints_that_do_not_verify_under_the_configured_key_are_refused(
    bank: Bank,
) -> None:
    keys = json.dumps({SIGNING_KEY_ID: other_public_key()})
    with bank.agent(REGISTRY_PUBLIC_KEYS=keys, HALT_ON_REGISTRY_INTEGRITY_FAILURE="false") as agent:
        tape = bank.upload(agent)
        agent.run_worker()
        assert_certificate_invalid(bank, agent, tape, CertificateFailure.BAD_SIGNATURE.value)
        agent.sync()
        failed = "SELECT failure_reason, 'stored' FROM checkpoints_seen GROUP BY 1"
        assert pairs(bank.database, failed) == {CheckpointFailure.BAD_SIGNATURE.value: "stored"}


class TamperCertificate(Faults):
    """CERTIFICATE_BODY on the registry once the first answer is stored, before the bank fetches it."""

    def __init__(self, bank: Bank) -> None:
        super().__init__()
        self._bank = bank
        self.certificate_id = ""

    def reached(self, point: FaultPoint) -> None:
        if point is FaultPoint.AFTER_RESPONSE_STORED and point not in self.passed:
            (certificate_id,) = self._bank.one(
                "SELECT certificate_id FROM registry_requests WHERE certificate_id IS NOT NULL LIMIT 1"
            )
            self.certificate_id = str(certificate_id)
            self._bank.doubles.tamper_certificate(self.certificate_id)
        super().reached(point)


def test_a_certificate_body_changed_at_the_registry_is_refused_with_body_hash_mismatch(bank: Bank) -> None:
    with bank.agent(TamperCertificate(bank), HALT_ON_REGISTRY_INTEGRITY_FAILURE="false") as agent:
        tape = bank.upload(agent)
        agent.run_worker()
        assert_certificate_invalid(bank, agent, tape, CertificateFailure.BODY_HASH_MISMATCH.value)


def test_a_tampered_certificate_fails_the_tape_halts_the_bank_and_a_retry_after_restore_recovers(
    bank: Bank,
) -> None:
    """SPEC_DECISIONS 61, end to end with the halt rule on: fail, break, halt; restore, retry, recover."""
    tamper = TamperCertificate(bank)
    with bank.agent(tamper) as agent:
        tape = bank.upload(agent)
        agent.run_worker()
        assert bank.tape(tape) == ("FAILED", "CERTIFICATE_INVALID")
        resume = "SELECT resume_status, failed_stage FROM tape_submissions WHERE submission_id = %s"
        assert bank.one(resume, (tape.submission_id,)) == ("SUBMITTED", "COMPLETED")
        broken = agent.report()
        assert broken.certificates.status is SectionStatus.BROKEN
        assert broken.status is IntegrityRunStatus.BROKEN
        failed = [(str(f.certificate_id), f.failure_reason) for f in broken.certificates.failures]
        assert failed == [(tamper.certificate_id, CertificateFinding.BODY_HASH_MISMATCH)]

        submissions = bank.doubles.member_stats("HARBOR")["submissions"]
        halted = bank.upload(agent, SECOND)
        agent.run_worker()
        assert bank.tape(halted) == ("FAILED", "REGISTRY_INTEGRITY_BROKEN"), "the halt rule is active"
        assert bank.doubles.member_stats("HARBOR")["submissions"] == submissions, "nothing was sent"

        assert bank.doubles.restore_certificate(tamper.certificate_id)["restored"] is True
        agent.retry(tape)
        agent.run_worker()
        assert bank.tape(tape) == ("COMPLETED", None)
        recovered = agent.report()
        assert recovered.certificates.status is SectionStatus.OK
        assert recovered.certificates.verified == recovered.certificates.total > 0
        assert [(str(f.certificate_id), f.failure_reason) for f in recovered.certificates.failures] == failed
        rows = "SELECT verified, count(*)::text FROM certificates WHERE certificate_id = %s GROUP BY 1"
        assert pairs(bank.database, rows, (tamper.certificate_id,)) == {"False": "1", "True": "1"}, (
            "the failed row stays as evidence beside the verified one"
        )
        hashes = (
            "SELECT verified, (delivered_body_sha256 = body_hash)::text FROM certificates "
            "WHERE certificate_id = %s"
        )
        assert pairs(bank.database, hashes, (tamper.certificate_id,)) == {"False": "false", "True": "true"}, (
            "the bank's hash of what arrived differs from the claim exactly in the tampered row"
        )
        assert agent.verify().certificates.status is SectionStatus.OK

        agent.retry(halted)
        agent.run_worker()
        assert bank.tape(halted) == ("COMPLETED", None), (
            "the halt rule released: a new submission goes through"
        )
        assert bank.doubles.member_stats("HARBOR")["submissions"] > submissions
        assert agent.report().status is IntegrityRunStatus.OK


def test_with_the_halt_rule_on_a_checkpoint_under_a_wrong_key_fails_first_and_nothing_is_submitted(
    bank: Bank,
) -> None:
    """SPEC_DECISIONS 60: a wrong REGISTRY_PUBLIC_KEYS is caught by checkpoint verification first."""
    keys = json.dumps({SIGNING_KEY_ID: other_public_key()})
    with bank.agent(REGISTRY_PUBLIC_KEYS=keys) as agent:
        bank.doubles.control(REGISTRY_URL, "POST", "/_test/checkpoint")
        tape = bank.upload(agent)
        agent.run_worker()
        failed = "SELECT failure_reason, 'stored' FROM checkpoints_seen GROUP BY 1"
        assert pairs(bank.database, failed) == {CheckpointFailure.BAD_SIGNATURE.value: "stored"}
        assert bank.tape(tape) == ("FAILED", "REGISTRY_INTEGRITY_BROKEN")
        assert bank.doubles.member_stats("HARBOR").get("submissions", 0) == 0
        assert bank.count("SELECT count(*) FROM certificates") == 0


def test_a_signed_certificate_for_another_member_is_refused_with_content_mismatch(bank: Bank) -> None:
    other = bank.doubles.members["NORTHGATE"].member_id
    with bank.agent(REGISTRY_MEMBER_ID=other) as agent:
        tape = bank.upload(agent)
        agent.run_worker()
        assert_certificate_invalid(bank, agent, tape, CertificateFailure.CONTENT_MISMATCH.value)


def test_a_stored_certificate_edited_by_a_superuser_is_found_by_deep_verification(bank: Bank) -> None:
    with bank.agent() as agent:
        bank.upload(agent)
        agent.run_worker()
        assert agent.verify().status is IntegrityRunStatus.OK
        (certificate_id,) = bank.one("SELECT certificate_id FROM certificates LIMIT 1")
        with bank.database.connect("admin") as connection:
            connection.execute("ALTER TABLE certificates DISABLE TRIGGER ALL")
            connection.execute(
                "UPDATE certificates SET body = jsonb_set(body, '{summary,locked}', '999') "
                "WHERE certificate_id = %s",
                (certificate_id,),
            )
            connection.execute("ALTER TABLE certificates ENABLE TRIGGER ALL")
        report = agent.verify()
    assert report.status is IntegrityRunStatus.BROKEN and report.certificates.status is SectionStatus.BROKEN
    assert [(f.certificate_id, f.failure_reason) for f in report.certificates.failures] == [
        (certificate_id, CertificateFinding.BODY_HASH_MISMATCH)
    ]


def test_a_stored_body_and_claimed_hash_edited_together_are_still_found(bank: Bank) -> None:
    """SPEC_DECISIONS 64: deep verification recomputes delivered_body_sha256 from the stored body."""
    with bank.agent() as agent:
        bank.upload(agent)
        agent.run_worker()
        certificate_id, body = bank.one("SELECT certificate_id, body FROM certificates LIMIT 1")
        assert isinstance(body, dict)
        forged = {**body, "summary": {**body["summary"], "locked": 999}}
        claimed = canonical_sha256(forged)
        with bank.database.connect("admin") as connection:
            connection.execute("ALTER TABLE certificates DISABLE TRIGGER ALL")
            connection.execute(
                "UPDATE certificates SET body = %s, body_hash = %s WHERE certificate_id = %s",
                (Jsonb(forged), claimed, certificate_id),
            )
            connection.execute("ALTER TABLE certificates ENABLE TRIGGER ALL")
        row = bank.one(
            "SELECT body_hash, delivered_body_sha256 FROM certificates WHERE certificate_id = %s",
            (certificate_id,),
        )
        assert row[0] == claimed != row[1], (
            "the claim now matches the forged body; only the bank's own hash differs"
        )
        report = agent.verify()
    assert report.certificates.status is SectionStatus.BROKEN
    assert (certificate_id, CertificateFinding.BODY_HASH_MISMATCH) in [
        (f.certificate_id, f.failure_reason) for f in report.certificates.failures
    ]


def test_no_md5_remains_in_the_certificate_schema(bank: Bank) -> None:
    definitions = bank.one(
        "SELECT string_agg(indexdef, ' ') FROM pg_indexes WHERE tablename = 'certificates'"
    )
    assert "md5" not in str(definitions[0])
    delivery = bank.one(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'uq_certificates_delivery'"
    )
    assert delivery == ("UNIQUE (certificate_id, delivered_body_sha256, signature)",)


def latest_verified(bank: Bank) -> tuple[int, int, str]:
    row = bank.one(
        "SELECT checkpoint_seq, last_event_seq, head_hash FROM checkpoints_seen WHERE verified "
        "ORDER BY checkpoint_seq DESC LIMIT 1"
    )
    seq, last, head = row
    assert isinstance(seq, int) and isinstance(last, int) and isinstance(head, str)
    return seq, last, head


def forged_body(checkpoint_seq: int, last_event_seq: int, head_hash: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "checkpoint_seq": checkpoint_seq,
        "last_event_seq": last_event_seq,
        "head_hash": head_hash,
        "signed_at": "2026-11-20T12:00:00Z",
    }


def failures(bank: Bank) -> list[tuple[object, ...]]:
    with bank.database.connect("admin") as connection:
        return connection.execute(
            "SELECT failure_reason, first_bad_event_seq FROM checkpoints_seen WHERE NOT verified "
            "ORDER BY received_at, checkpoint_seq"
        ).fetchall()


def test_every_checkpoint_failure_reason_is_stored_audited_and_acknowledged(bank: Bank) -> None:
    with bank.agent() as agent:
        bank.upload(agent)
        agent.run_worker()
        seq, last, head = latest_verified(bank)
        received = bank.one("SELECT count(*) FROM checkpoints_seen")

        duplicate = bank.doubles.checkpoints()[-1]
        bank.doubles.forge_checkpoint("HARBOR", duplicate["body"])
        agent.sync()
        assert bank.one("SELECT count(*) FROM checkpoints_seen") == received, "a duplicate stores nothing"

        bank.doubles.forge_checkpoint("HARBOR", forged_body(seq, last, "f" * 64))
        agent.sync()
        bank.doubles.forge_checkpoint("HARBOR", forged_body(seq + 1, last - 1, head))
        agent.sync()
        bank.doubles.forge_checkpoint("HARBOR", forged_body(seq + 1, last, "e" * 64))
        agent.sync()
        bank.doubles.tamper(mode="BAD_CHECKPOINT_SIGNATURE")
        agent.sync()
        bank.doubles.tamper(mode="EDIT_IN_PLACE", seq=last + 1)
        bank.doubles.control(REGISTRY_URL, "POST", "/_test/checkpoint")
        agent.sync()
        assert failures(bank) == [
            (CheckpointFailure.FORK.value, None),
            (CheckpointFailure.NON_MONOTONIC.value, None),
            (CheckpointFailure.HEAD_MISMATCH.value, last),
            (CheckpointFailure.BAD_SIGNATURE.value, None),
            (CheckpointFailure.CHAIN_BREAK.value, last + 1),
        ]
        rejected = bank.one("SELECT count(*) FROM audit_log WHERE action = 'CHECKPOINT_REJECTED'")
        assert rejected == (5,)
        assert bank.doubles.received()[-1]["path"].endswith("/ack"), "every failure is acknowledged"
        report = agent.report()
    assert report.registry_chain.status is SectionStatus.BROKEN
    assert {f.failure_reason for f in report.registry_chain.failures} == set(CheckpointFailure) - {
        CheckpointFailure.UNKNOWN_SIGNING_KEY
    }


def test_a_checkpoint_whose_headers_cannot_be_fetched_is_retried_not_stored(bank: Bank) -> None:
    with bank.agent() as agent:
        bank.upload(agent)
        agent.run_worker()
        received = bank.count("SELECT count(*) FROM checkpoints_seen")
        bank.doubles.control(REGISTRY_URL, "POST", "/_test/checkpoint")
        bank.doubles.fail_next(
            REGISTRY_URL, count=3, status=503, when="BEFORE_PROCESSING", path="/v1/ledger/events"
        )
        agent.sync()
        assert bank.count("SELECT count(*) FROM checkpoints_seen") == received
        agent.sync()
        assert bank.one("SELECT count(*), bool_and(verified) FROM checkpoints_seen") == (received + 1, True)


def test_the_halt_rule_stops_submissions_until_an_audited_operator_turns_it_off(bank: Bank) -> None:
    with bank.agent() as agent:
        bank.upload(agent)
        agent.run_worker()
        bank.doubles.tamper(mode="BAD_CHECKPOINT_SIGNATURE")
        agent.sync()
        submissions = bank.doubles.member_stats("HARBOR")["submissions"]
        halted = bank.upload(agent, SECOND)
        agent.run_worker()
        assert bank.tape(halted) == ("FAILED", "REGISTRY_INTEGRITY_BROKEN")
        assert bank.doubles.member_stats("HARBOR")["submissions"] == submissions
        assert bank.count("SELECT count(*) FROM loans WHERE state = 'PENDING'") > 0
        assert bank.one("SELECT count(*) FROM audit_log WHERE action = 'HALT_RULE_DISABLED'") == (0,)
        assert not agent.composition.startup_record().run("worker")
    with bank.agent(HALT_ON_REGISTRY_INTEGRITY_FAILURE="false") as operator:
        assert operator.composition.startup_record().run("worker")
        operator.retry(halted)
        operator.run_worker()
    assert bank.tape(halted) == ("COMPLETED", None)
    assert bank.doubles.member_stats("HARBOR")["submissions"] > submissions
    disabled = bank.one(
        "SELECT detail->>'setting', detail->>'value', detail->>'process' FROM audit_log "
        "WHERE action = 'HALT_RULE_DISABLED'"
    )
    assert disabled == ("HALT_ON_REGISTRY_INTEGRITY_FAILURE", "false", "worker")
