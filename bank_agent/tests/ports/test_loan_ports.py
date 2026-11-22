"""`LoanRepository`, `CheckRepository` and `TokenRepository`: loans and what the bank derived from them."""

from __future__ import annotations

import uuid

from bank_testkit.backends import PortWorld
from bank_testkit.memory.bank import vins
from bank_testkit.records import KEY_V1, KEY_V2, at, check_results, hex64, tokens
from onepledge_bank_agent.domain.enums import CheckOutcome, LoanState, TokenKind
from onepledge_bank_agent.domain.loan import Loan
from onepledge_bank_agent.domain.values import LenderLoanNumber, LoanTerms, Money, Token


def save(ports: PortWorld, loan_row: Loan) -> None:
    """Store a loan and commit."""
    with ports.units() as unit:
        unit.loans.save(loan_row)
        unit.commit()


def reread(ports: PortWorld, loan_row: Loan) -> Loan:
    """The loan as stored now."""
    with ports.units() as unit:
        return unit.loans.get_many([loan_row.loan_id], for_update=True)[loan_row.loan_id]


class TestLoans:
    """`loans`."""

    def test_loans_read_back_by_id_and_unknown_ids_are_left_out(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        made = ports.loans(tape.submission_id, vins(2))
        with ports.units() as unit:
            found = unit.loans.get_many([made[0].loan_id, made[1].loan_id, made[0].loan_id, uuid.uuid4()])
            assert unit.loans.get_many([]) == {}
        assert {i: f.record for i, f in found.items()} == {m.loan_id: m.record for m in made}

    def test_live_loans_by_vin_are_the_facilitys_loans_not_released(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        held, released, other = vins(3)
        mine = ports.loans(tape.submission_id, [held, released])
        elsewhere = ports.loans(tape.submission_id, [other], ports.other_facility.facility_id)[0]
        gone = mine[1]
        gone.lock(at(2))
        save(ports, gone)
        gone.release(at(3))
        save(ports, gone)
        facility = ports.facility.facility_id
        with ports.units() as unit:
            live = unit.loans.live_by_vin(facility, [held, released, other, held])
            assert unit.loans.live_by_vin(facility, []) == {}
            assert unit.loans.live_by_vin(ports.other_facility.facility_id, [other])[other].loan_id == (
                elsewhere.loan_id
            )
        assert {vin: found.loan_id for vin, found in live.items()} == {held: mine[0].loan_id}

    def test_a_released_vin_may_be_pledged_again_as_a_new_loan(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        vin = vins(1)[0]
        first = ports.loans(tape.submission_id, [vin])[0]
        first.lock(at(2))
        save(ports, first)
        first.release(at(3))
        save(ports, first)
        again = ports.loans(tape.submission_id, [vin])[0]
        with ports.units() as unit:
            live = unit.loans.live_by_vin(ports.facility.facility_id, [vin])
            both = unit.loans.get_many([first.loan_id, again.loan_id])
        assert live[vin].loan_id == again.loan_id
        assert {i: f.state for i, f in both.items()} == {
            first.loan_id: LoanState.RELEASED, again.loan_id: LoanState.PENDING,
        }  # fmt: skip

    def test_save_stores_state_terms_loan_number_and_balance(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        made = ports.loans(tape.submission_id, vins(1))[0]
        made.hold_local(at(2))
        save(ports, made)
        assert reread(ports, made).record == made.record
        terms = LoanTerms(
            made.terms.vin, made.terms.borrower_ref, made.terms.origination_date, Money(2_000_00)
        )
        made.replace_terms(terms, LenderLoanNumber("SAF-2"), Money(1_500_00), at(3))
        made.represent(at(4))
        save(ports, made)
        stored = reread(ports, made).record
        assert stored == made.record
        assert (stored.state, stored.current_balance, stored.lender_loan_number) == (
            LoanState.PENDING, Money(1_500_00), LenderLoanNumber("SAF-2"),
        )  # fmt: skip


class TestChecks:
    """`local_checks`."""

    def test_a_loan_is_checked_for_a_submission_once_its_four_results_are_stored(
        self, ports: PortWorld
    ) -> None:
        tape, later = ports.tape(at(1)), ports.tape(at(2))
        done, waiting = ports.loans(tape.submission_id, vins(2))
        with ports.units() as unit:
            unit.checks.add(tape.submission_id, done.loan_id, check_results(CheckOutcome.FAIL), at(3))
            assert unit.checks.checked(tape.submission_id, [done.loan_id]) == {done.loan_id}
            unit.commit()
        with ports.units() as unit:
            assert unit.checks.checked(tape.submission_id, [done.loan_id, waiting.loan_id]) == {done.loan_id}
            assert unit.checks.checked(later.submission_id, [done.loan_id]) == set()
            assert unit.checks.checked(tape.submission_id, []) == set()

    def test_fewer_than_four_results_do_not_count_as_checked(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        partial = ports.loans(tape.submission_id, vins(1))[0]
        with ports.units() as unit:
            unit.checks.add(tape.submission_id, partial.loan_id, check_results()[:3], at(2))
            unit.commit()
        with ports.units() as unit:
            assert unit.checks.checked(tape.submission_id, [partial.loan_id]) == set()


class TestTokens:
    """`loan_tokens`."""

    def test_tokens_read_back_in_key_version_then_kind_order(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        first, second, bare = ports.loans(tape.submission_id, vins(3))
        newer = tokens("first", KEY_V2)
        with ports.units() as unit:
            unit.tokens.add_many({first.loan_id: [*reversed(newer), *reversed(tokens("first"))]}, at(2))
            unit.tokens.add_many(
                {second.loan_id: tokens("second", kinds=(TokenKind.VIN, TokenKind.ASSET))}, at(2)
            )
            unit.commit()
        with ports.units() as unit:
            found = unit.tokens.for_loans([first.loan_id, second.loan_id, bare.loan_id])
            assert unit.tokens.for_loans([]) == {}
            assert unit.tokens.loans_with_tokens([first.loan_id, second.loan_id, bare.loan_id]) == {
                first.loan_id, second.loan_id,
            }  # fmt: skip
            assert unit.tokens.loans_with_tokens([]) == set()
        assert found[first.loan_id] == (*tokens("first"), *newer)
        assert found[second.loan_id] == tuple(tokens("second", kinds=(TokenKind.ASSET, TokenKind.VIN)))
        assert bare.loan_id not in found

    def test_a_stored_token_of_a_kind_and_version_is_left_unchanged(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        made = ports.loans(tape.submission_id, vins(1))[0]
        original = Token(TokenKind.ASSET, hex64("original"), KEY_V1)
        with ports.units() as unit:
            unit.tokens.add_many({made.loan_id: [original]}, at(2))
            unit.commit()
        replacement = Token(TokenKind.ASSET, hex64("replacement"), KEY_V1)
        with ports.units() as unit:
            unit.tokens.add_many(
                {made.loan_id: [replacement, Token(TokenKind.VIN, hex64("vin"), KEY_V1)]}, at(3)
            )
            unit.commit()
        with ports.units() as unit:
            assert unit.tokens.for_loans([made.loan_id])[made.loan_id] == (
                original, Token(TokenKind.VIN, hex64("vin"), KEY_V1),
            )  # fmt: skip

    def test_loans_are_found_by_their_asset_token_only(self, ports: PortWorld) -> None:
        tape = ports.tape(at(1))
        code = hex64("shared")
        released, current, vin_only = ports.loans(tape.submission_id, vins(3))
        with ports.units() as unit:
            unit.tokens.add_many(
                {
                    current.loan_id: [Token(TokenKind.ASSET, code, KEY_V1)],
                    released.loan_id: [Token(TokenKind.ASSET, code, KEY_V1)],
                    vin_only.loan_id: [Token(TokenKind.VIN, code, KEY_V1)],
                },
                at(2),
            )
            unit.commit()
        with ports.units() as unit:
            assert unit.tokens.loans_with_asset_token(code.value) == sorted(
                [released.loan_id, current.loan_id]
            )
            assert unit.tokens.loans_with_asset_token(hex64("nobody").value) == []
