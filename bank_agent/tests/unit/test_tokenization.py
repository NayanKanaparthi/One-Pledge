"""Token inputs (SPEC 9.2), the token rule and the two-holder blind OPRF (SPEC 9.3)."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import date

import oprf
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bank_testkit.data import CONTRACTS
from onepledge_bank_agent.domain.enums import TokenKind
from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.tokenization import (
    TOKENS_PER_LOAN,
    TokenInput,
    token_from_point,
    token_inputs,
)
from onepledge_bank_agent.domain.values import BorrowerRef, Lei, LoanTerms, Money
from onepledge_bank_agent.domain.vin import Vin
from onepledge_bank_agent.infrastructure.crypto.blinder import OprfBlinder, decode_point

pytestmark = pytest.mark.unit

VECTOR = json.loads((CONTRACTS / "examples" / "vectors" / "tokenization.json").read_text(encoding="utf-8"))
LEI = Lei("00000ONEPLEDGESUM134")


def terms(vin: str = "1HGCM82633A004352", cents: int = 2345000, day: date = date(2026, 8, 14)) -> LoanTerms:
    return LoanTerms(Vin.parse(vin), BorrowerRef("C0048213"), day, Money(cents))


def test_the_eight_inputs_are_the_vector_strings_in_kind_order() -> None:
    inputs = token_inputs(terms(), LEI)
    assert len(inputs) == TOKENS_PER_LOAN == 8
    expected = [(entry["kind"], entry["canonical_value"], entry["input_hex"]) for entry in VECTOR["tokens"]]
    assert [(i.kind.value, i.value, i.oprf_input().hex()) for i in inputs] == expected


@pytest.mark.parametrize(
    ("cents", "hundreds", "thousands"),
    [
        (2345000, "234", "23"),
        (2349999, "234", "23"),
        (2350000, "235", "23"),
        (10_000, "1", "0"),
        (99_999, "9", "0"),
    ],
)
def test_rounded_amounts_floor_the_cents(cents: int, hundreds: str, thousands: str) -> None:
    by_kind = {i.kind: i.value for i in token_inputs(terms(cents=cents), LEI)}
    prefix = f"{LEI.value}|C0048213|"
    assert by_kind[TokenKind.AMOUNT_EXACT] == prefix + str(cents)
    assert by_kind[TokenKind.AMOUNT_100] == prefix + hundreds
    assert by_kind[TokenKind.AMOUNT_1000] == prefix + thousands


def test_dates_give_the_day_and_the_month() -> None:
    by_kind = {i.kind: i.value for i in token_inputs(terms(day=date(2024, 1, 31)), LEI)}
    assert by_kind[TokenKind.DATE_DAY].endswith("|2024-01-31")
    assert by_kind[TokenKind.DATE_MONTH].endswith("|2024-01")


def test_an_input_never_shows_its_value() -> None:
    shown = repr(token_inputs(terms(), LEI))
    assert "1HGCM82633A004352" not in shown and "C0048213" not in shown and "ASSET" in shown


def test_the_token_of_a_point_is_the_vector_token_and_needs_32_bytes() -> None:
    for entry in VECTOR["tokens"]:
        point = base64.b64decode(entry["unblinded"])
        assert token_from_point(TokenKind(entry["kind"]), point) == entry["token"]
    with pytest.raises(InvalidValueError):
        token_from_point(TokenKind.ASSET, bytes(31))


def evaluate(scalar: bytes, element: str) -> str:
    """What one key holder computes: its scalar times the blinded point."""
    point = oprf.data(base64.b64decode(element))
    return base64.b64encode(bytes(oprf.mask(scalar).mask(point))).decode("ascii")


def test_the_blinder_reproduces_every_step_of_the_vector() -> None:
    blind = base64.b64decode(VECTOR["blind_scalar_b64"])
    holder_1 = base64.b64decode(VECTOR["holder_1_scalar_b64"])
    holder_2 = base64.b64decode(VECTOR["holder_2_scalar_b64"])
    blinder = OprfBlinder(lambda: blind)
    for entry in VECTOR["tokens"]:
        blinded = blinder.blind(bytes.fromhex(entry["input_hex"]))
        assert blinded.element == entry["blinded"]
        once = evaluate(holder_1, blinded.element)
        twice = evaluate(holder_2, once)
        assert (once, twice) == (entry["after_holder_1"], entry["after_holder_2"])
        unblinded = blinder.unblind(blinded, twice)
        assert unblinded is not None and base64.b64encode(unblinded).decode() == entry["unblinded"]
        assert repr(blinded) == "OprfBlinded(***)"


def fixed(scalar: bytes) -> Callable[[], bytes]:
    """A scalar source that always gives `scalar`."""
    return lambda: scalar


SCALARS = st.builds(lambda _: bytes(oprf.mask.random()), st.integers())


@settings(max_examples=40, deadline=None)
@given(st.binary(min_size=1, max_size=64), SCALARS, SCALARS, SCALARS, SCALARS)
def test_oprf_through_two_holders_equals_direct_evaluation_and_ignores_the_blind(
    message: bytes, holder_1: bytes, holder_2: bytes, blind_a: bytes, blind_b: bytes
) -> None:
    direct = bytes(oprf.mask(holder_2).mask(oprf.mask(holder_1).mask(oprf.data.hash(message))))
    seen = []
    for blind in (blind_a, blind_b):
        blinder = OprfBlinder(fixed(blind))
        blinded = blinder.blind(message)
        seen.append(blinded.element)
        assert blinder.unblind(blinded, evaluate(holder_2, evaluate(holder_1, blinded.element))) == direct
    assert blind_a == blind_b or seen[0] != seen[1], "the holders see a different element for each blind"


def test_an_evaluated_element_that_is_not_a_point_is_refused() -> None:
    blinder = OprfBlinder()
    blinded = blinder.blind(TokenInput(TokenKind.VIN, "1HGCM82633A004352").oprf_input())
    identity = base64.b64encode(bytes(32)).decode()
    for bad in ("", "abc", identity, base64.b64encode(b"\xff" * 32).decode(), "!" * 44):
        assert blinder.unblind(blinded, bad) is None, bad
    assert decode_point(identity) is None and decode_point(blinded.element) is not None
