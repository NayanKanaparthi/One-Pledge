"""`TapeRepository`: submissions, the duplicate file rule, progress and the worker's runnable order."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from bank_testkit.backends import PortWorld
from bank_testkit.records import at
from bank_testkit.tapes import SUMMIT_LAYOUT
from onepledge_bank_agent.domain.enums import FailureCode, TapeKind, TapeStatus
from onepledge_bank_agent.domain.errors import ConcurrentUploadError
from onepledge_bank_agent.domain.tape import NEXT_STAGE, UNFINISHED, TapeSubmission
from onepledge_bank_agent.domain.tape_file import TapeLayout


def stored(ports: PortWorld, tape: TapeSubmission) -> TapeSubmission:
    """The submission as stored now."""
    with ports.units() as unit:
        found = unit.tapes.get(tape.submission_id, for_update=True)
    assert found is not None
    return found


def save(ports: PortWorld, tape: TapeSubmission) -> None:
    """Store a submission's progress and commit."""
    with ports.units() as unit:
        unit.tapes.save(tape)
        unit.commit()


FORWARD = (
    TapeSubmission.mark_cleaned,
    TapeSubmission.mark_checked,
    TapeSubmission.mark_tokenized,
    TapeSubmission.mark_submitted,
    TapeSubmission.mark_completed,
)
"""The moves out of RECEIVED, CLEANED, CHECKED, TOKENIZED and SUBMITTED, in that order."""
RUNNING = (
    TapeStatus.RECEIVED,
    TapeStatus.CLEANED,
    TapeStatus.CHECKED,
    TapeStatus.TOKENIZED,
    TapeStatus.SUBMITTED,
)


class TestTapes:
    """`tape_submissions`."""

    def test_a_submission_and_its_layout_read_back_as_stored(self, ports: PortWorld) -> None:
        layout = TapeLayout(False, "a,b\n", ("a", "b"), "TRAILER 2\n")
        tape = ports.tape_of(at(1))
        with ports.units() as unit:
            unit.tapes.add(tape, layout)
            unit.commit()
        found = stored(ports, tape)
        assert (found.facts, found.progress) == (tape.facts, tape.progress)
        assert found.status is TapeStatus.RECEIVED
        with ports.units() as unit:
            assert unit.tapes.layout(tape.submission_id) == layout
            other = ports.tape_of(at(2))
            assert unit.tapes.get(other.submission_id) is None
            assert unit.tapes.layout(other.submission_id) is None

    def test_the_file_lookup_keys_on_facility_kind_and_file_together(self, ports: PortWorld) -> None:
        sha = "e" * 64
        first = ports.tape_of(at(1), file_sha256=sha)
        other_kind = ports.tape_of(at(2), kind=TapeKind.BACK_BOOK, file_sha256=sha)
        other_place = ports.tape_of(at(3), facility_id=ports.other_facility.facility_id, file_sha256=sha)
        with ports.units() as unit:
            for tape in (first, other_kind, other_place):
                unit.tapes.add(tape, SUMMIT_LAYOUT)
            unit.commit()
        with ports.units() as unit:
            summit = ports.facility.facility_id
            assert unit.tapes.find_by_file(summit, "PLEDGE", sha) == first.submission_id
            assert unit.tapes.find_by_file(summit, "BACK_BOOK", sha) == other_kind.submission_id
            assert unit.tapes.find_by_file(ports.other_facility.facility_id, "PLEDGE", sha) == (
                other_place.submission_id
            )
            assert unit.tapes.find_by_file(summit, "RELEASE", sha) is None
            assert unit.tapes.find_by_file(summit, "PLEDGE", "f" * 64) is None

    def test_a_second_insert_of_a_stored_file_raises_concurrent_upload(self, ports: PortWorld) -> None:
        sha = "d" * 64
        first = ports.tape_of(at(1), file_sha256=sha)
        with ports.units() as unit:
            unit.tapes.add(first, SUMMIT_LAYOUT)
            unit.commit()
        second = ports.tape_of(at(2), file_sha256=sha)
        with pytest.raises(ConcurrentUploadError), ports.units() as unit:
            unit.tapes.add(second, SUMMIT_LAYOUT)
        with ports.units() as unit:
            assert unit.history.submission_ids() == [first.submission_id]

    def test_save_stores_every_progress_field_of_each_legal_move(self, ports: PortWorld) -> None:
        """Each move is saved on its own, as the stages save it (the database guard allows one step)."""
        tape = ports.tape(at(1))
        assert not tape.record_failed_attempt(FailureCode.OUTSIDE_RECORDS_UNAVAILABLE, at(2), 3)
        save(ports, tape)
        found = stored(ports, tape)
        assert found.progress == tape.progress
        assert (found.progress.attempts, found.progress.next_attempt_at) == (1, at(2) + timedelta(seconds=2))
        tape.mark_cleaned(at(3))
        save(ports, tape)
        assert stored(ports, tape).progress == tape.progress
        assert stored(ports, tape).progress.attempts == 0
        tape.fail(FailureCode.KEY_HOLDER_REJECTED, at(4))
        save(ports, tape)
        failed = stored(ports, tape).progress
        assert failed == tape.progress
        assert (failed.status, failed.resume_status, failed.failed_stage) == (
            TapeStatus.FAILED, TapeStatus.CLEANED, TapeStatus.CHECKED,
        )  # fmt: skip
        tape.resume(at(5))
        save(ports, tape)
        assert stored(ports, tape).progress == tape.progress
        for move in (tape.mark_checked, tape.mark_tokenized, tape.mark_submitted, tape.mark_completed):
            move(at(6))
            save(ports, tape)
            assert stored(ports, tape).progress == tape.progress
        done = stored(ports, tape).progress
        assert done == tape.progress and done.completed_at == at(6)

    def test_runnable_follows_upload_order_and_skips_a_facilitys_later_tapes(self, ports: PortWorld) -> None:
        first, second = ports.tape_of(at(1)), ports.tape_of(at(2))
        elsewhere = ports.tape_of(at(3), facility_id=ports.other_facility.facility_id)
        with ports.units() as unit:
            for tape in (second, elsewhere, first):
                unit.tapes.add(tape, SUMMIT_LAYOUT)
            unit.commit()
        received = {TapeStatus.RECEIVED}
        with ports.units() as unit:
            assert unit.tapes.runnable(received, at(0), 10) == [first.submission_id, elsewhere.submission_id]
            assert unit.tapes.runnable(received, at(0), 1) == [first.submission_id]
            assert unit.tapes.runnable(set(), at(0), 10) == []
            assert unit.tapes.runnable({TapeStatus.CLEANED}, at(0), 10) == []

    def test_a_backing_off_tape_is_not_runnable_yet_and_still_blocks_its_facility(
        self, ports: PortWorld
    ) -> None:
        first, second = ports.tape(at(1)), ports.tape(at(2))
        assert not first.record_failed_attempt(FailureCode.OUTSIDE_RECORDS_UNAVAILABLE, at(3), 8)
        save(ports, first)
        due = first.progress.next_attempt_at
        assert due is not None
        with ports.units() as unit:
            assert unit.tapes.runnable({TapeStatus.RECEIVED}, at(3), 10) == []
            assert unit.tapes.runnable({TapeStatus.RECEIVED}, due, 10) == [first.submission_id]
        assert second.submission_id not in self._runnable(ports, due)

    def test_failed_and_completed_tapes_no_longer_block_later_tapes(self, ports: PortWorld) -> None:
        first, second, third = ports.tape(at(1)), ports.tape(at(2)), ports.tape(at(3))
        first.fail(FailureCode.OUTSIDE_RECORDS_REJECTED, at(4))
        save(ports, first)
        assert self._runnable(ports, at(5)) == [second.submission_id]
        for move in (second.mark_cleaned, second.mark_checked, second.mark_tokenized, second.mark_submitted):
            move(at(5))
            save(ports, second)
        second.mark_completed(at(6))
        save(ports, second)
        assert self._runnable(ports, at(7)) == [third.submission_id]

    @pytest.mark.parametrize("status", RUNNING)
    def test_every_legal_write_of_the_worker_is_stored_from_each_unfinished_status(
        self, ports: PortWorld, status: TapeStatus
    ) -> None:
        """SPEC_DECISIONS 68.7: attempts and backoff while running, FAILED from each status, and a retry."""
        assert set(RUNNING) == UNFINISHED
        tape = ports.tape(at(0))
        for move in FORWARD[: RUNNING.index(status)]:
            move(tape, at(1))
            save(ports, tape)
        stage = NEXT_STAGE[status]
        for attempt in (1, 2):
            assert not tape.record_failed_attempt(FailureCode.REGISTRY_UNAVAILABLE, at(1 + attempt), 3)
            save(ports, tape)
            backing_off = stored(ports, tape).progress
            assert backing_off == tape.progress
            assert (backing_off.status, backing_off.attempts) == (status, attempt)
            assert backing_off.next_attempt_at == at(1 + attempt) + timedelta(seconds=2**attempt)
        assert tape.record_failed_attempt(FailureCode.REGISTRY_UNAVAILABLE, at(4), 3)
        save(ports, tape)
        failed = stored(ports, tape).progress
        assert failed == tape.progress
        assert (failed.status, failed.resume_status, failed.failed_stage) == (
            TapeStatus.FAILED,
            status,
            stage,
        )
        assert (failed.failure_code, failed.attempts) == (FailureCode.REGISTRY_UNAVAILABLE, 3)
        assert tape.resume(at(5)) is status
        save(ports, tape)
        resumed = stored(ports, tape).progress
        assert resumed == tape.progress and (resumed.status, resumed.attempts) == (status, 0)
        tape.fail(FailureCode.OUTSIDE_RECORDS_REJECTED, at(6))
        save(ports, tape)
        assert stored(ports, tape).progress == tape.progress, "failed at once, without attempts"
        tape.resume(at(7))
        save(ports, tape)
        for move in FORWARD[RUNNING.index(status) :]:
            move(tape, at(8))
            save(ports, tape)
        assert stored(ports, tape).status is TapeStatus.COMPLETED

    @staticmethod
    def _runnable(ports: PortWorld, now: datetime) -> list[uuid.UUID]:
        with ports.units() as unit:
            return unit.tapes.runnable(UNFINISHED, now, 10)
