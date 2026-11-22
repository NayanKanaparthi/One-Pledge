"""`IngestTape` (SPEC 10.1) over in-memory ports."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import date

import pytest

from bank_testkit.tapes import summit_row, summit_tape
from bank_testkit.world import CrashError, World
from onepledge_bank_agent.application.faults import FaultPoint
from onepledge_bank_agent.application.ingest_tape import IngestCommand, IngestTape
from onepledge_bank_agent.domain.access_policy import AccessPolicy
from onepledge_bank_agent.domain.audit import Actor
from onepledge_bank_agent.domain.canonical_json import sha256_hex
from onepledge_bank_agent.domain.enums import AuditAction, AuditOutcome, Role, TapeKind, TapeStatus
from onepledge_bank_agent.domain.errors import (
    ConcurrentUploadError,
    DomainError,
    FacilityNotFoundError,
    ForbiddenError,
    TapeDuplicateError,
    TapeEmptyError,
    TapeHeaderInvalidError,
    TapeNotUtf8Error,
    TapeTooLargeError,
    ValidationFailedError,
)
from onepledge_bank_agent.domain.tape import TapeFacts, TapeSubmission
from onepledge_bank_agent.domain.tape_file import TapeFileReader

pytestmark = pytest.mark.unit

ANALYST = Actor.user(uuid.uuid4(), Role.ANALYST)
OFFICER = Actor.user(uuid.uuid4(), Role.CREDIT_OFFICER)
DATA = summit_tape([summit_row(), summit_row(vin="5YJ3E1EA2KF317000")])


@pytest.fixture
def world() -> World:
    return World()


def command(world: World) -> IngestCommand:
    return IngestCommand(
        world.facility.facility_id, TapeKind.PLEDGE, date(2026, 10, 15), "../S01 tape.csv", DATA, ANALYST,
        request_id="req-7",
    )  # fmt: skip


def run(world: World, request: IngestCommand) -> TapeSubmission:
    return IngestTape(world.units, TapeFileReader(), AccessPolicy(), world.runtime).execute(request)


def test_an_upload_stores_the_submission_every_raw_row_and_one_audit_entry(world: World) -> None:
    tape = run(world, command(world))
    state = world.store.state
    assert (
        tape.status is TapeStatus.RECEIVED and state.tapes[tape.submission_id].status is TapeStatus.RECEIVED
    )
    facts = tape.facts
    assert (facts.file_name, facts.row_count, facts.file_sha256) == ("S01_tape.csv", 2, sha256_hex(DATA))
    assert facts.uploaded_by == ANALYST.user_id and facts.uploaded_at == world.clock.now()
    assert [r.row_number for r in state.raw_rows[tape.submission_id]] == [1, 2]
    (uploaded,) = world.audit()
    assert uploaded.action is AuditAction.TAPE_UPLOADED and uploaded.object_id == tape.submission_id
    assert uploaded.actor == ANALYST and uploaded.request_id == "req-7"
    assert dict(uploaded.detail) == {
        "kind": "PLEDGE", "source": "FILE_UPLOAD", "row_count": 2, "file_sha256": facts.file_sha256,
        "rows_sha256": facts.rows_sha256,
    }  # fmt: skip
    assert world.faults.passed == [FaultPoint.AFTER_INTAKE_COMMIT]
    assert world.log.events == [
        (
            "info",
            "tape_received",
            {"submission_id": str(tape.submission_id), "kind": "PLEDGE", "rows": 2,
             "user_id": str(ANALYST.user_id)},
        )
    ]  # fmt: skip


def test_a_release_tape_needs_a_credit_officer_and_a_refusal_is_audited(world: World) -> None:
    release = replace(command(world), kind=TapeKind.RELEASE, data=b"VIN\n1HGCM82633A004352\n")
    with pytest.raises(ForbiddenError):
        run(world, release)
    (denied,) = world.audit()
    assert (denied.action, denied.outcome) == (AuditAction.ACCESS_DENIED, AuditOutcome.DENIED)
    assert dict(denied.detail) == {"method": "POST", "route": "/v1/tapes", "operation": "uploadTape"}
    assert not world.store.state.tapes
    assert world.log.events == [
        (
            "warning",
            "access_denied",
            {"method": "POST", "route": "/v1/tapes", "role": "ANALYST", "user_id": str(ANALYST.user_id)},
        )
    ]
    assert run(world, replace(release, actor=OFFICER)).facts.kind is TapeKind.RELEASE


def test_an_admin_or_the_system_cannot_upload(world: World) -> None:
    for actor in (Actor.user(uuid.uuid4(), Role.ADMIN), Actor.system()):
        with pytest.raises(ForbiddenError):
            run(world, replace(command(world), actor=actor))
    assert len(world.audit(AuditAction.ACCESS_DENIED)) == 2


type Change = Callable[[World, IngestCommand], IngestCommand]
REFUSALS: list[tuple[Change, type[DomainError], str]] = [
    (lambda w, c: replace(c, facility_id=uuid.uuid4()), FacilityNotFoundError, "FACILITY_NOT_FOUND"),
    (lambda w, c: replace(c, data=b""), TapeEmptyError, "TAPE_EMPTY"),
    (lambda w, c: replace(c, data="caf\xe9".encode("latin-1")), TapeNotUtf8Error, "TAPE_NOT_UTF8"),
    (lambda w, c: replace(c, data=b"Loan #,VIN\nx,y\n"), TapeHeaderInvalidError, "TAPE_HEADER_INVALID"),
    (lambda w, c: replace(c, data=b"x" * (5 * 1024 * 1024 + 1)), TapeTooLargeError, "TAPE_TOO_LARGE"),
    (lambda w, c: replace(c, as_of_date=date(2026, 11, 21)), ValidationFailedError, "VALIDATION_ERROR"),
    (lambda w, c: replace(c, as_of_date=date(1999, 12, 31)), ValidationFailedError, "VALIDATION_ERROR"),
]


@pytest.mark.parametrize(("change", "error", "code"), REFUSALS)
def test_a_refused_upload_stores_nothing_but_one_audit_entry_with_the_code(
    world: World, change: Change, error: type[DomainError], code: str
) -> None:
    request = change(world, command(world))
    with pytest.raises(error) as raised:
        run(world, request)
    assert not world.store.state.tapes and not world.store.state.raw_rows
    (rejected,) = world.audit()
    assert (rejected.action, rejected.outcome, rejected.object_id) == (
        AuditAction.TAPE_UPLOAD_REJECTED,
        AuditOutcome.FAILURE,
        None,
    )
    assert rejected.detail["error_code"] == code and rejected.detail["file_sha256"] == sha256_hex(
        request.data
    )
    if error is ValidationFailedError:
        assert [(d.field, d.issue) for d in raised.value.details] == [("as_of_date", "OUT_OF_RANGE")]
    assert world.log.events == [
        (
            "warning",
            "tape_upload_rejected",
            {"kind": "PLEDGE", "error_code": code, "user_id": str(ANALYST.user_id)},
        )
    ]


def test_the_as_of_date_may_be_today(world: World) -> None:
    assert (
        run(world, replace(command(world), as_of_date=world.clock.now().date())).status is TapeStatus.RECEIVED
    )


def test_a_duplicate_file_is_refused_with_the_existing_submission(world: World) -> None:
    first = run(world, command(world))
    with pytest.raises(TapeDuplicateError) as raised:
        run(world, replace(command(world), as_of_date=date(2026, 10, 1)))
    assert raised.value.existing_submission_id == first.submission_id
    assert [(d.field, d.issue) for d in raised.value.details] == [("submission_id", str(first.submission_id))]
    rejected = world.audit(AuditAction.TAPE_UPLOAD_REJECTED)
    assert rejected[0].detail["existing_submission_id"] == str(first.submission_id)
    assert len(world.store.state.tapes) == 1
    other_kind = run(world, replace(command(world), kind=TapeKind.BACK_BOOK))
    assert other_kind.submission_id != first.submission_id


def test_losing_a_race_for_the_same_file_is_reported_as_a_duplicate_of_the_winner(world: World) -> None:
    request = command(world)
    parsed = TapeFileReader().read(request.data, request.kind)
    winner = TapeSubmission.receive(
        TapeFacts(
            uuid.uuid4(),
            request.facility_id,
            request.kind,
            request.source,
            "other.csv",
            parsed.file_sha256,
            parsed.rows_sha256,
            request.as_of_date,
            len(parsed.rows),
            uuid.uuid4(),
            world.clock.now(),
        )
    )
    world.store.race_next_tape_insert, world.store.race_winner = True, winner
    with pytest.raises(TapeDuplicateError) as raised:
        run(world, command(world))
    assert raised.value.existing_submission_id == winner.submission_id
    assert world.audit(AuditAction.TAPE_UPLOAD_REJECTED)[0].detail["error_code"] == "TAPE_DUPLICATE"


def test_a_race_whose_winner_rolled_back_is_still_refused(world: World) -> None:
    world.store.race_next_tape_insert = True
    with pytest.raises(ConcurrentUploadError):
        run(world, command(world))
    assert not world.store.state.tapes and len(world.audit(AuditAction.TAPE_UPLOAD_REJECTED)) == 1


def test_a_crash_right_after_the_commit_leaves_a_complete_submission(world: World) -> None:
    world.faults.arm(FaultPoint.AFTER_INTAKE_COMMIT)
    with pytest.raises(CrashError):
        run(world, command(world))
    (tape,) = world.store.state.tapes.values()
    assert tape.status is TapeStatus.RECEIVED and len(world.store.state.raw_rows[tape.submission_id]) == 2
