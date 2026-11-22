"""`HistoryReader`: every stored tape and the audit log, as deep verification reads them."""

from __future__ import annotations

import uuid

from bank_testkit.backends import PortWorld
from bank_testkit.records import at, raw_rows
from bank_testkit.tapes import SUMMIT_HEADER, SUMMIT_LAYOUT
from onepledge_bank_agent.domain.enums import TapeSource
from onepledge_bank_agent.domain.raw_evidence import StoredRow, StoredTape


class TestHistory:
    """The stored forms of each tape, in upload order."""

    def test_submissions_are_listed_in_upload_order(self, ports: PortWorld) -> None:
        later, first = ports.tape_of(at(5)), ports.tape_of(at(1))
        same_time = sorted([ports.tape_of(at(3)), ports.tape_of(at(3))], key=lambda t: t.submission_id)
        with ports.units() as unit:
            for tape in (later, same_time[1], first, same_time[0]):
                unit.tapes.add(tape, SUMMIT_LAYOUT)
            unit.commit()
        with ports.units() as unit:
            assert unit.history.submission_ids() == [
                first.submission_id,
                same_time[0].submission_id,
                same_time[1].submission_id,
                later.submission_id,
            ]

    def test_a_tape_reads_back_with_its_facts_layout_and_rows_in_row_order(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        rows = raw_rows(SUMMIT_HEADER, 3)
        with ports.units() as unit:
            unit.raw_rows.add_many(tape.submission_id, [rows[2], rows[0], rows[1]], at(2))
            unit.commit()
        with ports.units() as unit:
            found = unit.history.tape(tape.submission_id)
            assert unit.history.tape(uuid.uuid4()) is None
        facts = tape.facts
        assert found == StoredTape(
            facts.submission_id, TapeSource.FILE_UPLOAD, facts.facility_id, facts.as_of_date,
            facts.file_sha256, facts.rows_sha256, facts.row_count, SUMMIT_LAYOUT,
            tuple(StoredRow(r.row_number, r.text, r.cells, dict(r.payload), r.row_sha256) for r in rows),
        )  # fmt: skip
