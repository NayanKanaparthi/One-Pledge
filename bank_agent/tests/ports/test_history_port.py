"""`HistoryReader`: every stored tape and the audit log, as deep verification reads them."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from bank_testkit.backends import PortWorld
from bank_testkit.memory.bank import vins
from bank_testkit.parity import parity
from bank_testkit.records import (
    KEY_V1,
    alert,
    answer,
    at,
    check_results,
    item,
    outcome,
    planned,
    raw_rows,
    request,
    tokens,
)
from bank_testkit.tapes import SUMMIT_HEADER, SUMMIT_LAYOUT
from onepledge_bank_agent.application.ports import NewItem
from onepledge_bank_agent.domain.entities import RegistryRequest
from onepledge_bank_agent.domain.enums import (
    CleaningAction,
    CleaningProblem,
    RegistryItemResult,
    RequestOrigin,
    RowDisposition,
    TapeSource,
)
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.raw_evidence import StoredRow, StoredTape
from onepledge_bank_agent.domain.registry_records import ItemOutcome, RegistryAnswer
from onepledge_bank_agent.domain.stage_digests import DigestRow, StagePart, body_sha256, row
from onepledge_bank_agent.domain.values import CleaningIssue, KeyVersion

KEY_V2 = KeyVersion(2, 2)


class TestHistory:
    """The stored forms of each tape, in upload order."""

    @parity("HistoryReader.submission_ids", "TapeRepository.add")
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

    @parity("HistoryReader.loan_ids")
    def test_every_loan_id_is_read_for_the_anchor_check(self, ports: PortWorld) -> None:
        """SPEC_DECISIONS 89 b: every loan must have its creating entry, so every id is read."""
        with ports.units() as unit:
            assert unit.history.loan_ids() == set()
        first = ports.loans(ports.tape(at(1)).submission_id, vins(2))
        second = ports.loans(ports.tape(at(2)).submission_id, vins(1, start=5))
        with ports.units() as unit:
            assert unit.history.loan_ids() == {loan.loan_id for loan in (*first, *second)}

    @parity("HistoryReader.tape", "RawRowRepository.add_many")
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
            facts.submission_id, TapeSource.FILE_UPLOAD, facts.kind, facts.facility_id, facts.as_of_date,
            facts.file_sha256, facts.rows_sha256, facts.row_count, SUMMIT_LAYOUT,
            tuple(StoredRow(r.row_number, r.text, r.cells, dict(r.payload), r.row_sha256) for r in rows),
        )  # fmt: skip


@dataclass(frozen=True)
class Staged:
    """One submission through every stage, and what its rows must read back as."""

    submission_id: uuid.UUID
    loans: tuple[Loan, ...]
    items: tuple[NewItem, ...]
    batches: tuple[RegistryRequest, ...]
    resubmit: RegistryRequest
    locked: ItemOutcome
    answer: RegistryAnswer


ISSUES = (
    CleaningIssue("vin", CleaningProblem.WHITESPACE_TRIMMED, CleaningAction.FIXED),
    CleaningIssue("borrower_ref", CleaningProblem.WHITESPACE_TRIMMED, CleaningAction.FIXED),
    CleaningIssue("borrower_ref", CleaningProblem.BORROWER_REF_NORMALISED, CleaningAction.FIXED),
)
"""Issues written out of the order a digest reads them in."""


def staged(ports: PortWorld, start: int = 0) -> Staged:
    """A submission with items, issues, checks, tokens of two key versions, two tape requests written
    out of batch order (one answered) and a resubmission, each written in an order the reads must fix."""
    tape = ports.tape(at(1 + start))
    rows = raw_rows(SUMMIT_HEADER, 3)
    loans = tuple(ports.loans(tape.submission_id, vins(2, start=start + 40)))
    items: tuple[NewItem, ...] = (
        item(uuid.uuid4(), 1, RowDisposition.ACCEPTED_NEW, loans[0].loan_id),
        item(uuid.uuid4(), 2, RowDisposition.ACCEPTED_NEW, loans[1].loan_id),
        replace(item(uuid.uuid4(), 3, RowDisposition.REJECTED, None), issues=ISSUES),
    )
    with ports.units() as unit:
        unit.raw_rows.add_many(tape.submission_id, rows, at(2))
        unit.commit()
    with ports.units() as unit:
        raw = {r.row_number: r.raw_id for r in unit.raw_rows.list_for(tape.submission_id)}
    items = tuple(replace(i, raw_id=raw[i.row_number]) for i in items)
    batches = (request(tape.submission_id, 0), request(tape.submission_id, 1))
    resubmit = request(tape.submission_id, 0, origin=RequestOrigin.RESUBMIT)
    with ports.units() as unit:
        unit.items.add_many(tape.submission_id, list(reversed(items)))
        for held in reversed(loans):
            unit.checks.add(tape.submission_id, held.loan_id, list(reversed(check_results())), at(3))
        unit.tokens.add_many(
            {
                held.loan_id: [*tokens(held.loan_id, KEY_V2), *reversed(tokens(held.loan_id))]
                for held in loans
            },
            at(4),
        )
        unit.requests.add(batches[1], planned([loans[1].loan_id]))
        unit.requests.add(batches[0], planned([loans[0].loan_id]))
        unit.requests.add(resubmit, planned([loans[0].loan_id]))
        unit.commit()
    with ports.units() as unit:
        (first,) = unit.requests.items(batches[0].request_id)
        locked, kept = outcome(first.item_ref, RegistryItemResult.LOCKED)
        answered = answer(batches[0], [locked])
        unit.requests.mark_sent(batches[0].request_id, at(5))
        unit.requests.store_answer(batches[0].request_id, answered, at(6))
        unit.requests.store_result(batches[0].request_id, locked, kept)
        unit.commit()
    return Staged(tape.submission_id, loans, items, batches, resubmit, locked, answered)


def expected_requests(ports: PortWorld, made: Staged) -> list[DigestRow]:
    """The two tape requests in batch order, every answer and result column as written."""
    with ports.units() as unit:
        first, second = (unit.requests.items(r.request_id) for r in made.batches)
        stored = [unit.requests.get(r.request_id) for r in made.batches]
    assert stored[0] is not None and stored[1] is not None
    done = made.locked
    return [
        row(
            made.batches[0].idempotency_key.value, str(KEY_V1), 1, 201, at(6), made.answer.submission_id,
            made.answer.as_of_seq, made.answer.certificate_id, body_sha256(made.answer.body),
            [row(first[0].item_ref, 0, made.loans[0].loan_id, "LOCKED", False, None, None, [], None,
                 done.lock_id, None)],
        ),
        row(
            made.batches[1].idempotency_key.value, str(KEY_V1), 1, None, None, None, None, None, None,
            [row(second[0].item_ref, 0, made.loans[1].loan_id, None, None, None, None, [], None, None, None)],
        ),
    ]  # fmt: skip


class TestStageRows:
    """SPEC_DECISIONS 107 and 127: the rows every stage digest covers, the same from both stores."""

    @parity("HistoryReader.stage_rows")
    def test_each_part_reads_its_submissions_rows_in_the_digest_order(self, ports: PortWorld) -> None:
        made, other = staged(ports), staged(ports, start=10)
        loans = sorted(made.loans, key=lambda held: str(held.loan_id))
        with ports.units() as unit:
            found = {part: unit.history.stage_rows(made.submission_id, part) for part in StagePart}
        assert found[StagePart.ITEMS] == [row(i.row_number, i.disposition, i.loan_id) for i in made.items]
        assert found[StagePart.ISSUES] == [
            row(3, "borrower_ref", "BORROWER_REF_NORMALISED", "FIXED"),
            row(3, "borrower_ref", "WHITESPACE_TRIMMED", "FIXED"),
            row(3, "vin", "WHITESPACE_TRIMMED", "FIXED"),
        ]
        assert found[StagePart.CHECKS] == [
            row(held.loan_id, c.check_type, c.outcome, c.reason, c.evidence)
            for held in loans
            for c in sorted(check_results(), key=lambda c: c.check_type.value)
        ]
        assert found[StagePart.TOKENS] == [
            row(held.loan_id, t.kind, str(t.key_version), t.value.value)
            for held in loans
            for t in sorted(tokens(held.loan_id), key=lambda t: t.kind.value)
        ], "only the key version of the submission's requests"
        assert found[StagePart.REQUESTS] == expected_requests(ports, made)
        with ports.units() as unit:
            assert unit.history.stage_rows(other.submission_id, StagePart.ITEMS) == [
                row(i.row_number, i.disposition, i.loan_id) for i in other.items
            ], "each submission reads only its own rows"

    @parity("HistoryReader.stage_rows")
    def test_a_submission_without_rows_reads_empty_in_every_part(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        with ports.units() as unit:
            assert {part: unit.history.stage_rows(tape.submission_id, part) for part in StagePart} == {
                part: [] for part in StagePart
            }

    @parity("HistoryReader.request_rows")
    def test_requests_read_in_the_order_given_with_a_missing_one_named(self, ports: PortWorld) -> None:
        made = staged(ports)
        missing = uuid.uuid4()
        tape_rows = expected_requests(ports, made)
        with ports.units() as unit:
            assert unit.history.request_rows([]) == []
            found = unit.history.request_rows(
                [made.batches[1].request_id, missing, made.batches[0].request_id]
            )
        assert found == [tape_rows[1], row(str(missing), None), tape_rows[0]]

    @parity("HistoryReader.reference_rows")
    def test_lenders_and_facilities_read_in_id_order_with_every_seeded_column(self, ports: PortWorld) -> None:
        with ports.units() as unit:
            found = unit.history.reference_rows()
        facilities = sorted((ports.facility, ports.other_facility), key=lambda f: str(f.facility_id))
        assert found == {
            "lenders": [row(ports.lender.lender_id, ports.lender.legal_name, ports.lender.lei.value)],
            "facilities": [
                row(f.facility_id, f.lender_id, f.code, f.name, f.credit_limit_cents, f.advance_rate_bps,
                    f.status, f.opened_on)
                for f in facilities
            ],
        }  # fmt: skip

    @parity("HistoryReader.states")
    def test_the_replayed_columns_are_read_for_loans_tapes_alerts_and_users(self, ports: PortWorld) -> None:
        made = staged(ports)
        made_alert = alert(made.loans[0].loan_id)
        with ports.units() as unit:
            unit.alerts.add(made_alert)
            unit.commit()
        with ports.units() as unit:
            found = {table: unit.history.states(table) for table in TABLES}
        assert {key: found["loans"][key] for key in (held.loan_id for held in made.loans)} == {
            held.loan_id: ("PENDING",) for held in made.loans
        }
        assert found["tape_submissions"][made.submission_id] == ("RECEIVED",)
        assert found["alerts"] == {made_alert.record.alert_id: ("NEW", "OPEN")}
        assert found["users"] == {ports.user: ("ANALYST", "true")}

    @parity("HistoryReader.loan_values")
    def test_each_loans_values_are_read_as_stored(self, ports: PortWorld) -> None:
        made = staged(ports)
        with ports.units() as unit:
            found = unit.history.loan_values()
        assert found == {
            held.loan_id: row(
                held.terms.vin.value, held.terms.borrower_ref.value, held.terms.origination_date,
                held.terms.original_amount.cents, held.record.current_balance.cents,
                held.record.lender_loan_number.value,
            )
            for held in made.loans
        }  # fmt: skip


TABLES = ("loans", "tape_submissions", "alerts", "users")
