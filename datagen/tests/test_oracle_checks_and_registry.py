"""The oracle's local checks (SPEC 11) and registry rules (registry.yaml), with their boundaries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import pytest

from onepledge_datagen.application.invariants import OutsideRecordsIndex
from onepledge_datagen.domain.entities import ContractRecord, PaymentRecord, TitleRecord
from onepledge_datagen.domain.oracle.checks import LocalChecks, holds_locally
from onepledge_datagen.domain.oracle.lender_names import names_match, normalise_lender_name
from onepledge_datagen.domain.oracle.model import CheckOutcome, CheckType, Reason, Terms
from onepledge_datagen.domain.oracle.registry import RegistryModel, token_inputs

pytestmark = pytest.mark.unit

AS_OF = date(2026, 11, 5)
VIN = "1HGCM82633A004352"
LENDER = "Summit Auto Finance LLC"
LEI = "00000ONEPLEDGESUM134"


class Records:
    """In-memory outside records."""

    def __init__(
        self,
        title: TitleRecord | None = None,
        payments: Sequence[date] = (),
        contracts: Sequence[ContractRecord] = (),
    ) -> None:
        self._title = title
        self._payments = [PaymentRecord(f"p{i}", VIN, day, 100, "HARBOR") for i, day in enumerate(payments)]
        self._contracts = list(contracts)

    def title(self, vin: str) -> TitleRecord | None:
        return self._title

    def payments(self, vin: str, first: date, last: date) -> Sequence[PaymentRecord]:
        return [p for p in self._payments if first <= p.paid_on <= last]

    def contracts(self, vin: str) -> Sequence[ContractRecord]:
        return self._contracts


def terms(age_days: int, cents: int = 2_345_017) -> Terms:
    origination = AS_OF - timedelta(days=age_days)
    return Terms(VIN, "C1234567", origination.isoformat(), cents, "SAF-1", 100)


def contract(
    age_days: int, cents: int = 2_345_017, lender: str = LENDER, signed_delta: int = 0
) -> ContractRecord:
    signed = AS_OF - timedelta(days=age_days) + timedelta(days=signed_delta)
    return ContractRecord("c1", VIN, lender, cents, signed, "0" * 64)


def run(age: int, records: Records) -> dict[CheckType, tuple[CheckOutcome, Reason]]:
    return {r.check_type: (r.outcome, r.reason) for r in LocalChecks(records).run(terms(age), LENDER, AS_OF)}


TITLE = TitleRecord(VIN, "NJ", "CLEAN", "SUMMIT AUTO FINANCE, L.L.C.", date(2026, 1, 1))


@pytest.mark.parametrize(
    ("age", "outcome", "reason"),
    [
        (29, CheckOutcome.UNKNOWN, Reason.TITLE_PENDING_WINDOW),
        (30, CheckOutcome.UNKNOWN, Reason.TITLE_PENDING_WINDOW),
        (31, CheckOutcome.FAIL, Reason.NO_TITLE_RECORD),
    ],
)
def test_title_pending_window_boundary(age: int, outcome: CheckOutcome, reason: Reason) -> None:
    results = run(age, Records())
    assert results[CheckType.TITLE_EXISTS] == (outcome, reason)
    assert results[CheckType.LIENHOLDER_MATCH] == (CheckOutcome.UNKNOWN, reason)


@pytest.mark.parametrize(
    ("age", "outcome", "reason"),
    [
        (44, CheckOutcome.UNKNOWN, Reason.FIRST_PAYMENT_NOT_DUE),
        (45, CheckOutcome.FAIL, Reason.NO_PAYMENT_IN_WINDOW),
    ],
)
def test_first_payment_grace_boundary(age: int, outcome: CheckOutcome, reason: Reason) -> None:
    assert run(age, Records(TITLE))[CheckType.PAYMENTS_RECEIVED] == (outcome, reason)


@pytest.mark.parametrize(("paid_days_before", "passes"), [(90, True), (0, True), (91, False), (-1, False)])
def test_payment_window_includes_both_ends(paid_days_before: int, passes: bool) -> None:
    records = Records(TITLE, payments=[AS_OF - timedelta(days=paid_days_before)])
    outcome = run(200, records)[CheckType.PAYMENTS_RECEIVED][0]
    assert outcome == (CheckOutcome.PASS if passes else CheckOutcome.FAIL)


def test_lienholder_rules() -> None:
    assert run(200, Records(TITLE))[CheckType.LIENHOLDER_MATCH] == (
        CheckOutcome.PASS,
        Reason.LIENHOLDER_MATCHES,
    )
    other = TitleRecord(VIN, "NJ", "CLEAN", "Crestline Auto Credit Corp", None)
    assert run(200, Records(other))[CheckType.LIENHOLDER_MATCH] == (
        CheckOutcome.FAIL,
        Reason.LIENHOLDER_MISMATCH,
    )
    none = TitleRecord(VIN, "NJ", "CLEAN", None, None)
    assert run(200, Records(none))[CheckType.LIENHOLDER_MATCH][0] == CheckOutcome.FAIL


@pytest.mark.parametrize(
    ("contracts", "reason"),
    [
        ((), Reason.NO_CONTRACT_RECORD),
        ((contract(200, lender="Crestline Auto Credit Corp"),), Reason.CONTRACT_LENDER_MISMATCH),
        ((contract(200, cents=1),), Reason.CONTRACT_TERMS_MISMATCH),
        ((contract(200, signed_delta=-4),), Reason.CONTRACT_TERMS_MISMATCH),
        ((contract(200, signed_delta=-3),), Reason.CONTRACT_MATCHES),
        ((contract(200, cents=1), contract(200)), Reason.CONTRACT_MATCHES),
        ((contract(200, lender="SUMMIT AUTO FINANCE LLC"),), Reason.CONTRACT_MATCHES),
    ],
)
def test_contract_rules(contracts: tuple[ContractRecord, ...], reason: Reason) -> None:
    assert run(200, Records(TITLE, contracts=contracts))[CheckType.CONTRACT_PRESENT][1] == reason


def test_hold_decision() -> None:
    young = LocalChecks(Records(contracts=(contract(10),))).run(terms(10), LENDER, AS_OF)
    assert not holds_locally(young)
    old = LocalChecks(Records(contracts=(contract(100),))).run(terms(100), LENDER, AS_OF)
    assert holds_locally(old)


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("Summit Auto Finance LLC", "SUMMIT AUTO FINANCE, L.L.C.", True),
        ("Smith & Sons Co", "SMITH AND SONS COMPANY", True),
        ("Acme Holdings LP", "ACME HOLDINGS L.P.", True),
        ("Acme Inc Corp", "Acme", True),
        ("Acme", "Acme Credit", False),
        ("LLC", "LLC", False),
        (None, "Acme", False),
    ],
)
def test_lender_name_normalisation(first: str | None, second: str, same: bool) -> None:
    assert names_match(first, second) is same
    assert normalise_lender_name("  Summit   Auto  Finance, LLC ") == "SUMMIT AUTO FINANCE"


def inputs(
    vin: str = VIN, borrower: str = "C1", cents: int = 2_345_017, day: str = "2026-08-14"
) -> dict[str, str]:
    return token_inputs(LEI, Terms(vin, borrower, day, cents, "SAF-1", 1))


def test_token_inputs_follow_spec_9_2() -> None:
    values = inputs(borrower="C0048213", cents=2_345_000)
    assert values["ASSET"] == "AUTO|" + VIN
    assert values["AMOUNT_100"] == f"{LEI}|C0048213|234"
    assert values["AMOUNT_1000"] == f"{LEI}|C0048213|23"
    assert values["DATE_MONTH"] == f"{LEI}|C0048213|2026-08"


def test_registry_rules_a_b_c_and_release() -> None:
    model = RegistryModel()
    assert model.lock("A", inputs()).result == "LOCKED"
    assert model.lock("A", inputs()).result == "LOCKED"
    exact = model.lock("B", inputs())
    assert (exact.result, exact.match_kind, exact.match_basis) == ("CONFLICT", "EXACT", "ASSET")
    assert exact.new_case is not None and model.lock("B", inputs()).new_case is None
    likely = model.lock("C", inputs(cents=2_400_000))
    assert (likely.match_kind, likely.match_basis) == ("LIKELY", "ASSET")
    fields = model.lock("B", inputs(vin="2HGCM82633A004352"))
    assert (fields.match_kind, fields.match_basis) == ("LIKELY", "FIELDS")
    assert "ASSET" not in fields.kinds_matched
    near = model.lock("B", inputs(vin="3HGCM82633A004352", cents=2_345_090, day="2026-08-20"))
    assert near.result == "LOCKED"
    assert model.release("B", "AUTO|" + VIN) == "REJECTED"
    assert model.release("A", "AUTO|" + VIN) == "RELEASED"
    assert model.lock("B", inputs()).result == "LOCKED"


WINDOW_FIRST, WINDOW_LAST = date(2026, 8, 7), date(2026, 11, 5)


def index_with_payments(*days: date) -> OutsideRecordsIndex:
    """The oracle's own outside records index, holding one payment per given day for VIN."""
    payments = [PaymentRecord(f"p-{day.isoformat()}", VIN, day, 10_000, "HARBOR") for day in days]
    other = PaymentRecord("p-other", "2HGCM82633A004352", WINDOW_FIRST, 10_000, "HARBOR")
    return OutsideRecordsIndex([], [*payments, other], [])


@pytest.mark.parametrize(
    ("day", "inside"),
    [
        (WINDOW_FIRST - timedelta(days=1), False),
        (WINDOW_FIRST, True),
        (WINDOW_FIRST + timedelta(days=1), True),
        (WINDOW_LAST - timedelta(days=1), True),
        (WINDOW_LAST, True),
        (WINDOW_LAST + timedelta(days=1), False),
    ],
)
def test_the_oracle_payment_window_is_inclusive_at_both_ends(day: date, inside: bool) -> None:
    found = index_with_payments(day).payments(VIN, WINDOW_FIRST, WINDOW_LAST)
    assert [p.paid_on for p in found] == ([day] if inside else [])


def test_the_oracle_payment_window_keeps_every_payment_inside_in_date_order() -> None:
    days = [WINDOW_LAST + timedelta(days=1), WINDOW_LAST, WINDOW_FIRST, WINDOW_FIRST - timedelta(days=1)]
    found = index_with_payments(*days).payments(VIN, WINDOW_FIRST, WINDOW_LAST)
    assert [p.paid_on for p in found] == [WINDOW_FIRST, WINDOW_LAST]
    assert index_with_payments().payments(VIN, WINDOW_FIRST, WINDOW_LAST) == []


@pytest.mark.parametrize("exact_first", [True, False])
def test_a_repeated_conflict_reports_the_items_own_match(exact_first: bool) -> None:
    model = RegistryModel()
    assert model.lock("A", inputs()).result == "LOCKED"
    changed = inputs(cents=2_345_017 + 40_000)
    first_inputs, second_inputs = (inputs(), changed) if exact_first else (changed, inputs())
    first, second = model.lock("B", first_inputs), model.lock("B", second_inputs)
    for decision, exact in ((first, exact_first), (second, not exact_first)):
        assert decision.result == "CONFLICT"
        assert decision.match_kind == ("EXACT" if exact else "LIKELY")
        assert ("AMOUNT_EXACT" in decision.kinds_matched) is exact
    assert first.new_case is not None and second.new_case is None
    assert len(model.cases) == 1 and model.cases[0].match_kind == first.match_kind
