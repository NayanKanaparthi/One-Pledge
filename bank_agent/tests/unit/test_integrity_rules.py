"""The certificates and registry messages sections and the halt rule.

SPEC 15.4 and 15.5; SPEC_DECISIONS 56, 57 and 61.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from bank_testkit.world import World
from onepledge_bank_agent.application.integrity_sections import certificates_section, deep_findings_of
from onepledge_bank_agent.application.submit_stage import HaltRule
from onepledge_bank_agent.domain.dependency_errors import RegistryIntegrityBrokenError
from onepledge_bank_agent.domain.entities import Certificate, Checkpoint
from onepledge_bank_agent.domain.enums import (
    CertificateFailure,
    CheckpointFailure,
    QuarantineReason,
    TapeKind,
)
from onepledge_bank_agent.domain.integrity import (
    CertificateFinding,
    CertificateProblem,
    QuarantineFinding,
    SectionStatus,
    registry_messages_section,
)
from onepledge_bank_agent.domain.registry_records import QuarantinedMessage, QuarantinedPayload
from onepledge_bank_agent.domain.storable import StorableTimestamp
from onepledge_bank_agent.domain.values import Sha256Hex

pytestmark = pytest.mark.unit

AT = datetime(2026, 11, 20, 12, tzinfo=UTC)
FIRST, SECOND = uuid.uuid4(), uuid.uuid4()


def certificate(request_id: uuid.UUID, failure: CertificateFailure | None, body: int = 1) -> Certificate:
    return Certificate(
        certificate_row_id=uuid.uuid4(),
        certificate_id=uuid.UUID(int=request_id.int ^ 1),
        request_id=request_id,
        registry_submission_id=uuid.uuid4(),
        kind=TapeKind.PLEDGE,
        as_of_seq=3,
        issued_at=StorableTimestamp(AT),
        body={"locked": body},
        body_hash=Sha256Hex("a" * 64),
        signature="c2lnbmF0dXJl",
        signing_key_id="registry-1",
        failure=failure,
        verified_at=AT,
        received_at=AT,
        delivered_body_sha256=Sha256Hex(str(body) * 64),
    )


BAD = CertificateFailure.BODY_HASH_MISMATCH
BAD_FINDING = CertificateFinding.BODY_HASH_MISMATCH


def test_a_request_without_a_certificate_yet_is_counted_but_breaks_nothing() -> None:
    section = certificates_section(2, [certificate(FIRST, None)], [], [])
    assert (section.status, section.total, section.verified, section.failures) == (SectionStatus.OK, 2, 1, ())


def test_a_failed_row_with_no_verified_one_breaks_the_section_until_a_verified_row_proves_the_request() -> (
    None
):
    failed = certificate(FIRST, BAD, body=2)
    broken = certificates_section(2, [failed, certificate(SECOND, None)], [], [])
    assert (broken.status, broken.verified) == (SectionStatus.BROKEN, 1)
    recovered = certificates_section(2, [failed, certificate(SECOND, None), certificate(FIRST, None)], [], [])
    assert (recovered.status, recovered.verified) == (SectionStatus.OK, 2)
    assert recovered.failures == (CertificateProblem(failed.certificate_id, BAD_FINDING),), (
        "the evidence stays listed"
    )


def test_a_deep_finding_breaks_the_section_and_a_stored_failed_row_is_not_one() -> None:
    failed, verified = certificate(FIRST, BAD, body=2), certificate(FIRST, None)
    receipt = CertificateProblem(failed.certificate_id, BAD_FINDING)
    finding = CertificateProblem(verified.certificate_id, CertificateFinding.BAD_SIGNATURE)
    assert deep_findings_of([receipt, finding], [failed, verified]) == [finding]
    section = certificates_section(1, [failed, verified], [finding], [])
    assert section.status is SectionStatus.BROKEN and set(section.failures) == {receipt, finding}
    assert certificates_section(
        1, [failed, verified], deep_findings_of([receipt], [failed, verified]), []
    ).status is (SectionStatus.OK)


@pytest.mark.parametrize(("held", "status"), [(0, SectionStatus.OK), (1, SectionStatus.BROKEN)])
def test_any_quarantined_message_breaks_the_registry_messages_section(
    held: int, status: SectionStatus
) -> None:
    messages = [QuarantineFinding(uuid.uuid4(), "ALERT", "SCHEMA_MISMATCH") for _ in range(held)]
    section = registry_messages_section(messages)
    assert (section.status, section.quarantined, section.messages) == (status, held, tuple(messages))


def failed_checkpoint(world: World) -> None:
    with world.units() as unit:
        unit.evidence.checkpoints.add(
            Checkpoint(
                uuid.uuid4(),
                1,
                5,
                Sha256Hex("b" * 64),
                StorableTimestamp(AT),
                "c2ln",
                "registry-1",
                {},
                AT,
                AT,
                CheckpointFailure.BAD_SIGNATURE,
                None,
            )
        )
        unit.commit()


def unproven_certificate(world: World) -> None:
    with world.units() as unit:
        unit.evidence.certificates.add(certificate(FIRST, BAD, body=2))
        unit.commit()


def quarantined(message_type: str) -> object:
    def store(world: World) -> None:
        payload = QuarantinedPayload(QuarantineReason.SCHEMA_MISMATCH, {"x": 1}, "e" * 64)
        with world.units() as unit:
            unit.evidence.quarantine.add(
                QuarantinedMessage(uuid.uuid4(), uuid.uuid4(), 9, message_type, payload, AT)
            )
            unit.commit()

    return store


CONDITIONS = [
    ("a failed checkpoint", failed_checkpoint, True),
    ("a request with only a failed certificate", unproven_certificate, True),
    ("a quarantined CHECKPOINT message", quarantined("CHECKPOINT"), True),
    ("a quarantined ALERT message", quarantined("ALERT"), False),
]


@pytest.mark.parametrize(("name", "setup", "halts"), CONDITIONS, ids=[c[0] for c in CONDITIONS])
def test_each_of_the_three_conditions_activates_the_halt_rule_unless_it_is_off(
    name: str, setup: object, halts: bool
) -> None:
    world = World()
    rule, off = HaltRule(world.units, True, world.log), HaltRule(world.units, False, world.log)
    assert not rule.active()
    assert callable(setup)
    setup(world)
    assert rule.active() is halts, name
    assert not off.active()
    if halts:
        with pytest.raises(RegistryIntegrityBrokenError):
            rule.check()
        assert world.log.events[-1][1] == "halt_rule_applied"
    else:
        rule.check()


def test_a_verified_certificate_for_the_same_request_releases_the_halt_rule() -> None:
    world = World()
    unproven_certificate(world)
    rule = HaltRule(world.units, True, world.log)
    assert rule.active()
    with world.units() as unit:
        assert unit.evidence.certificates.add(certificate(FIRST, None))
        assert not unit.evidence.certificates.add(certificate(FIRST, None)), (
            "the same delivery is stored once"
        )
        unit.commit()
    assert not rule.active()
