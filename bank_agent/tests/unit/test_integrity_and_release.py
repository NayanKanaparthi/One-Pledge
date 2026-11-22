"""Deep verification, the live integrity report and release instructions, over the in-memory ports.

SPEC 15.5, 15.6 and 14.6. Every finding is provoked by changing stored state or the
scripted registry the way a superuser or a compromised registry would, then read back.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import pytest

from bank_testkit.memory.bank import AS_OF, MemoryBank, vins
from bank_testkit.memory.registry import Tamper
from onepledge_bank_agent.application.create_release_instruction import (
    CreateReleaseInstruction,
    ReleaseCommand,
)
from onepledge_bank_agent.domain.dependency_errors import RegistryUnavailableError
from onepledge_bank_agent.domain.enums import (
    AuditAction,
    CertificateFailure,
    CheckpointFailure,
    FailureReason,
    IntegrityRunStatus,
    TapeStatus,
)
from onepledge_bank_agent.domain.errors import (
    FacilityNotFoundError,
    ForbiddenError,
    LoanNotLockedError,
    TapeDuplicateError,
    ValidationFailedError,
)
from onepledge_bank_agent.domain.integrity import DatabaseControl, SectionStatus
from onepledge_bank_agent.domain.values import Sha256Hex

pytestmark = pytest.mark.unit


@pytest.fixture
def bank() -> MemoryBank:
    bank = MemoryBank()
    bank.pledge(vins(3))
    bank.registry.checkpoint()
    bank.outbox_sync().run()
    return bank


def test_a_clean_bank_verifies_ok_in_every_section_and_the_run_is_stored_and_audited(
    bank: MemoryBank,
) -> None:
    before = bank.report()
    assert (before.audit_log.status, before.raw_data.status) == (
        SectionStatus.NOT_VERIFIED,
        SectionStatus.NOT_VERIFIED,
    )
    assert before.status is IntegrityRunStatus.OK and before.last_deep_verification_at is None
    report = bank.verify()
    assert report.status is IntegrityRunStatus.OK
    assert report.section_statuses() == dict.fromkeys(
        ("registry_chain", "certificates", "audit_log", "raw_data", "database_controls", "registry_messages"),
        "OK",
    )
    assert (report.raw_data.submissions_checked, report.raw_data.rows_checked) == (1, 3)
    (run,) = bank.world.audit(AuditAction.INTEGRITY_VERIFICATION_RUN)
    assert (run.detail["status"], run.detail["via"]) == ("OK", "cli")
    live = bank.report()
    assert live.audit_log.status is SectionStatus.OK and live.last_deep_verification_at is not None


def test_an_edited_audit_entry_is_located_by_seq(bank: MemoryBank) -> None:
    log = bank.world.store.state.audit_log
    log[4] = replace(log[4], detail={**log[4].detail, "forged": True})
    report = bank.verify()
    assert (report.audit_log.status, report.audit_log.first_bad_seq) == (SectionStatus.BROKEN, 5)
    assert bank.report().audit_log.first_bad_seq == 5, "the live report keeps the last deep finding"


def test_a_raw_row_that_no_longer_rebuilds_its_file_names_its_submission(bank: MemoryBank) -> None:
    (submission_id,) = bank.world.store.state.tapes
    rows = bank.world.store.state.raw_rows[submission_id]
    rows[0] = replace(rows[0], text=rows[0].text.replace("SAF", "XAF"))
    report = bank.verify()
    assert report.raw_data.status is SectionStatus.BROKEN
    assert report.raw_data.bad_submission_ids == (submission_id,)


def test_a_stored_certificate_body_edited_with_its_claimed_hash_is_still_found(bank: MemoryBank) -> None:
    certificates = bank.world.store.state.certificates
    first = certificates[0]
    forged = {**first.body, "as_of_seq": 999}
    certificates[0] = replace(first, body=forged, body_hash=Sha256Hex("c" * 64))
    report = bank.verify()
    assert report.certificates.status is SectionStatus.BROKEN
    assert [(f.certificate_id, f.failure_reason) for f in report.certificates.failures] == [
        (first.certificate_id, CertificateFailure.BODY_HASH_MISMATCH)
    ]


def test_a_stored_signature_edited_in_place_is_found_on_a_verified_row(bank: MemoryBank) -> None:
    certificates = bank.world.store.state.certificates
    first = certificates[1]
    certificates[1] = replace(
        first, signature="A" + first.signature[1:] if first.signature[0] != "A" else "B"
    )
    report = bank.verify()
    assert (first.certificate_id, CertificateFailure.BAD_SIGNATURE) in [
        (f.certificate_id, f.failure_reason) for f in report.certificates.failures
    ]


def test_a_history_rewritten_before_the_latest_checkpoint_is_found_by_deep_verification(
    bank: MemoryBank,
) -> None:
    bank.registry.rewrite_event(1)
    report = bank.verify()
    assert report.registry_chain.status is SectionStatus.BROKEN
    (finding,) = report.registry_chain.failures
    assert (finding.failure_reason, finding.first_bad_event_seq) == (CheckpointFailure.CHAIN_BREAK, 2), (
        "event 1 hashes as rewritten; event 2 no longer links to it"
    )


def test_deep_verification_without_the_registry_stores_nothing(bank: MemoryBank) -> None:
    bank.registry.fail("event_headers", RegistryUnavailableError(503, FailureReason.SERVER_ERROR))
    with pytest.raises(RegistryUnavailableError):
        bank.verify()
    assert bank.world.store.state.integrity_runs == []
    assert bank.world.audit(AuditAction.INTEGRITY_VERIFICATION_RUN) == []


def test_a_control_switched_off_breaks_the_live_report_at_once(bank: MemoryBank) -> None:
    bank.controls.state[DatabaseControl.ALERT_STATE_GUARD_TRIGGER_ENABLED] = False
    report = bank.report()
    assert (
        report.database_controls.status is SectionStatus.BROKEN and report.status is IntegrityRunStatus.BROKEN
    )
    assert dict(report.database_controls.controls)[DatabaseControl.ALERT_STATE_GUARD_TRIGGER_ENABLED] is False


def test_a_failed_certificate_found_by_a_deep_run_and_later_proven_is_ok_in_the_live_report() -> None:
    bank = MemoryBank()
    tape = bank.upload(vins(1))
    bank.run(tape, until=TapeStatus.SUBMITTED)
    (certificate_id,) = bank.registry.certificate_ids()
    bank.registry.tamper(certificate_id, Tamper.CONTENT)
    bank.run(tape)
    assert bank.verify().certificates.status is SectionStatus.BROKEN, "the deep run lists the failed row"
    bank.registry.restore(certificate_id)
    bank.retry(tape)
    bank.run(tape)
    live = bank.report()
    assert live.certificates.status is SectionStatus.OK, "a stored failed row is not a deep finding"
    assert [f.failure_reason for f in live.certificates.failures] == [CertificateFailure.CONTENT_MISMATCH]


# -- release instructions (SPEC 14.6) -------------------------------------------------------------


def locked(bank: MemoryBank) -> list[uuid.UUID]:
    return sorted(loan.loan_id for loan in bank.loans().values())


@pytest.mark.parametrize(
    ("change", "field", "issue"),
    [
        (lambda ids: (), "loan_ids", "OUT_OF_RANGE"),
        (lambda ids: (ids[0], ids[0]), "loan_ids", "DUPLICATE"),
    ],
)
def test_a_malformed_release_is_refused_and_audited(
    bank: MemoryBank, change: object, field: str, issue: str
) -> None:
    assert callable(change)
    with pytest.raises(ValidationFailedError) as raised:
        bank.release(list(change(locked(bank))))
    assert [(d.field, d.issue) for d in raised.value.details] == [(field, issue)]
    (refused,) = bank.world.audit(AuditAction.TAPE_UPLOAD_REJECTED)
    assert refused.detail["error_code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("as_of", [date(1999, 12, 31), date(2026, 11, 21)])
def test_a_release_as_of_date_outside_2000_to_today_is_refused(bank: MemoryBank, as_of: date) -> None:
    with pytest.raises(ValidationFailedError) as raised:
        bank.release(locked(bank)[:1], as_of)
    assert [(d.field, d.issue) for d in raised.value.details] == [("as_of_date", "OUT_OF_RANGE")]


def test_a_release_of_a_loan_that_is_not_locked_here_names_it(bank: MemoryBank) -> None:
    stranger = uuid.uuid4()
    with pytest.raises(LoanNotLockedError) as raised:
        bank.release([locked(bank)[0], stranger])
    assert raised.value.loan_ids == (stranger,)
    assert bank.world.store.state.tapes.keys() == {next(iter(bank.world.store.state.tapes))}


def test_the_same_release_twice_is_a_duplicate_of_the_first(bank: MemoryBank) -> None:
    first = bank.release(locked(bank)[:1], AS_OF)
    with pytest.raises(TapeDuplicateError) as raised:
        bank.release(locked(bank)[:1], AS_OF)
    assert raised.value.existing_submission_id == first.submission_id


def test_a_release_for_an_unknown_facility_or_by_an_analyst_is_refused(bank: MemoryBank) -> None:
    bank.world.store.state.facilities.pop(bank.world.facility.facility_id)
    with pytest.raises(FacilityNotFoundError):
        bank.release(locked(bank)[:1])
    with pytest.raises(ForbiddenError):
        CreateReleaseInstruction(bank.world.units, bank.authoriser(), bank.world.runtime).execute(
            ReleaseCommand(
                bank.world.facility.facility_id, AS_OF, tuple(locked(bank)[:1]), bank.requester(bank.analyst)
            )
        )
