"""Money spellings, calendar arithmetic, amortisation, seeded streams and the catalog."""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from onepledge_datagen.domain import catalog
from onepledge_datagen.domain.amortisation import balance_after_months, monthly_payment_cents
from onepledge_datagen.domain.calendar import add_months, day_month_year, us_date, whole_months_between
from onepledge_datagen.domain.money import (
    amount_with_one_decimal,
    amount_without_decimals,
    dollar_amount,
    dollar_amount_without_cents,
    plain_amount,
)
from onepledge_datagen.domain.randomness import RandomStream, SeededStreams

pytestmark = pytest.mark.unit


def lei_is_valid(lei: str) -> bool:
    """ISO 17442 / ISO 7064 mod 97-10: letters as 10 to 35, the whole number mod 97 equals 1."""
    digits = "".join(str(int(char, 36)) for char in lei)
    return len(lei) == 20 and int(digits) % 97 == 1


def test_amount_spellings() -> None:
    assert plain_amount(2_345_017) == "23450.17"
    assert plain_amount(-2_345_000) == "-23450.00"
    assert dollar_amount(2_345_017) == "$23,450.17"
    assert dollar_amount(95_005) == "$950.05"
    assert dollar_amount_without_cents(2_345_000) == "$23,450"
    assert amount_without_decimals(2_345_000) == "23450"
    assert amount_with_one_decimal(2_345_050) == "23450.5"


def test_calendar() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2025, 11, 30), 3) == date(2026, 2, 28)
    assert whole_months_between(date(2026, 1, 15), date(2026, 3, 14)) == 1
    assert whole_months_between(date(2026, 1, 15), date(2026, 3, 15)) == 2
    assert whole_months_between(date(2026, 3, 15), date(2026, 1, 15)) == 0
    assert us_date(date(2026, 8, 4)) == "08/04/2026"
    assert day_month_year(date(2026, 8, 14)) == "14-Aug-2026"


def test_amortisation_known_values() -> None:
    assert monthly_payment_cents(2_345_017, 189, 60) == 60_702
    assert balance_after_months(2_345_017, 189, 60, 0) == 2_345_017
    assert balance_after_months(2_345_017, 189, 60, 60) == 1


@given(
    st.integers(min_value=800_000, max_value=5_500_099),
    st.integers(min_value=49, max_value=249),
    st.sampled_from(catalog.TERMS),
    st.integers(min_value=0, max_value=90),
)
def test_property_balance_never_exceeds_original_and_falls(
    principal: int, apr: int, term: int, months: int
) -> None:
    balance = balance_after_months(principal, apr, term, months)
    assert 1 <= balance <= principal
    assert balance_after_months(principal, apr, term, months + 1) <= balance


def test_streams_are_deterministic_and_independent() -> None:
    first, second = SeededStreams(7).stream("a"), SeededStreams(7).stream("a")
    assert [first.integer(0, 1000) for _ in range(20)] == [second.integer(0, 1000) for _ in range(20)]
    assert SeededStreams(7).stream("a").new_uuid() != SeededStreams(7).stream("b").new_uuid()
    assert SeededStreams(7).stream("a").new_uuid() != SeededStreams(8).stream("a").new_uuid()


def test_triangular_stays_in_range_and_peaks_near_the_mode() -> None:
    stream = RandomStream(1, "triangular")
    values = [stream.triangular_int(8_000, 21_000, 55_000) for _ in range(4000)]
    assert min(values) >= 8_000 and max(values) <= 55_000
    middle = sorted(values)[len(values) // 2]
    assert 20_000 <= middle <= 32_000


def test_weighted_and_sample_helpers() -> None:
    stream = RandomStream(3, "helpers")
    assert stream.weighted(("a", "b"), (0, 5)) == "b"
    assert sorted(stream.sample([1, 2, 3, 4], 4)) == [1, 2, 3, 4]
    assert sorted(stream.shuffled([3, 1, 2])) == [1, 2, 3]
    assert len(stream.digits(9)) == 9
    assert date(2026, 1, 1) <= stream.date_between(date(2026, 1, 1), date(2026, 1, 3)) <= date(2026, 1, 3)


def test_catalog_matches_spec() -> None:
    assert [b.code for b in catalog.BANKS] == ["HARBOR", "NORTHGATE", "CALDER"]
    assert all(lei_is_valid(lender.lei) for lender in catalog.LENDERS)
    assert [f.code for f in catalog.FACILITIES] == [
        "HARBOR-SUMMIT", "HARBOR-LAKESIDE", "NORTHGATE-SUMMIT", "NORTHGATE-LAKESIDE", "CALDER-SUMMIT",
    ]  # fmt: skip
    assert {f.advance_rate_bps for f in catalog.FACILITIES if f.lender_code == "SUMMIT"} == {8500}
    assert sum(s.baseline_full for s in catalog.STEPS) == 4700
    assert sum(s.baseline_small for s in catalog.STEPS) == 397
    assert len(catalog.MANUFACTURERS) >= 20
    assert [s.step_id for s in catalog.STEPS if s.concurrent_group] == ["S14", "S15"]
    assert [s.uploader_role for s in catalog.STEPS if s.kind.value == "RELEASE"] == ["CREDIT_OFFICER"]
