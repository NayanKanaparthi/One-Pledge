"""Digits are ASCII digits, trimming is ASCII trimming, integers have one parser (SPEC_DECISIONS 37, 45).

Each case was first reproduced failing (WP2 review 1, F2 and F6): Python's `\\d`, `int`,
`Decimal`, `str.strip` and `str.upper` accept or map characters other than the ASCII ones.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from onepledge_bank_agent.domain.cleaning.amounts import clean_amount
from onepledge_bank_agent.domain.cleaning.dates import clean_origination_date
from onepledge_bank_agent.domain.cleaning.model import FieldResult
from onepledge_bank_agent.domain.cleaning.rows import clean_field
from onepledge_bank_agent.domain.cleaning.text_fields import clean_borrower_ref
from onepledge_bank_agent.domain.cleaning.vin_field import clean_vin
from onepledge_bank_agent.domain.enums import CleaningAction, CleaningProblem
from onepledge_bank_agent.domain.errors import InvalidValueError
from onepledge_bank_agent.domain.text_rules import parse_integer, trim
from onepledge_bank_agent.domain.values import Money
from onepledge_bank_agent.infrastructure.operator_errors import OperatorError
from onepledge_bank_agent.infrastructure.settings import RuntimeSettings

pytestmark = pytest.mark.unit

AS_OF = date(2026, 9, 30)
FULL_WIDTH = str.maketrans("0123456789", "\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19")
ARABIC_INDIC = str.maketrans("0123456789", "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669")


def problems(result: FieldResult[Any]) -> list[CleaningProblem]:
    return [issue.problem for issue in result.issues if issue.action is CleaningAction.REJECTED]


@pytest.mark.parametrize("text", ["23450.17", "$23,450.17", "1,234.50"])
@pytest.mark.parametrize("digits", [FULL_WIDTH, ARABIC_INDIC], ids=["full-width", "arabic-indic"])
def test_an_amount_in_non_ascii_digits_is_unparseable(text: str, digits: dict[int, str]) -> None:
    result = clean_amount("original_amount", text.translate(digits))
    assert result.value is None and problems(result) == [CleaningProblem.AMOUNT_UNPARSEABLE]
    assert clean_amount("original_amount", text).value is not None


@pytest.mark.parametrize(
    "text",
    [
        "2345\u00b2",
        "1e3",
        "0x10",
        "1_000",
        "+100.00",
        "\u0661\u0660\u0660.\u0660\u0660",
        "1,00.00",
        "12,3456.00",
    ],
)
def test_other_amount_spellings_are_unparseable(text: str) -> None:
    assert problems(clean_amount("original_amount", text)) == [CleaningProblem.AMOUNT_UNPARSEABLE]


@pytest.mark.parametrize("text", ["2025-03-15", "03/15/2025", "15-Mar-2025"])
@pytest.mark.parametrize("digits", [FULL_WIDTH, ARABIC_INDIC], ids=["full-width", "arabic-indic"])
def test_a_date_in_non_ascii_digits_is_unparseable(text: str, digits: dict[int, str]) -> None:
    result = clean_origination_date(text.translate(digits), AS_OF)
    assert result.value is None and problems(result) == [CleaningProblem.DATE_UNPARSEABLE]
    assert clean_origination_date(text, AS_OF).value is not None


def test_a_month_name_must_be_ascii() -> None:
    assert clean_origination_date("15-MAR-2025", AS_OF).value == date(2025, 3, 15)
    assert problems(clean_origination_date("15-M\u017fr-2025", AS_OF)) == [CleaningProblem.DATE_UNPARSEABLE]


@pytest.mark.parametrize(
    "text",
    [
        "1HGCM82633A0043\u0405" + "2",
        "1hgcm82633a00435\u017f",
        "1HGCM82633A00435\u0410",
        "\uff11HGCM82633A004352",
    ],
    ids=["cyrillic-dze", "long-s", "cyrillic-a", "full-width-one"],
)
def test_a_vin_with_a_non_ascii_character_is_refused(text: str) -> None:
    result = clean_vin(text)
    assert result.value is None
    assert CleaningProblem.VIN_CHARACTER_INVALID in problems(
        result
    ) or CleaningProblem.VIN_LENGTH_INVALID in problems(result)


@pytest.mark.parametrize(
    "text", ["C12+34567", "C1234\u017f67", "C\uff11234567", "C1234567\u200b", "C12\u00a034567"]
)
def test_a_borrower_reference_keeps_only_the_listed_separators(text: str) -> None:
    assert problems(clean_borrower_ref(text)) == [CleaningProblem.BORROWER_REF_INVALID]


@pytest.mark.parametrize("text", ["c-123 456", "C_123.456", "c/1234567"])
def test_the_listed_separators_are_removed_and_the_reference_upper_cased(text: str) -> None:
    result = clean_borrower_ref(text)
    assert result.value is not None and problems(result) == []


@pytest.mark.parametrize("space", ["\u00a0", "\u2003", "\u200b", "\u3000"])
def test_a_cell_is_trimmed_of_ascii_whitespace_only(space: str) -> None:
    result = clean_field(
        "original_amount", space + "23450.17", lambda text: clean_amount("original_amount", text)
    )
    assert result.value is None and problems(result) == [CleaningProblem.AMOUNT_UNPARSEABLE]
    padded = clean_field(
        "original_amount", "  23450.17  ", lambda text: clean_amount("original_amount", text)
    )
    assert padded.value == Money(2345017)
    assert trim(space + "x") == space + "x"


INTEGER_CASES = [
    *[(text, "INVALID_INTEGER") for text in ("+1", " 1", "1.0", "", "\u00b2", "\u0663", "1" * 19)],
    ("-1", "OUT_OF_RANGE"),
    ("101", "OUT_OF_RANGE"),
]


@pytest.mark.parametrize(("text", "issue"), INTEGER_CASES)
def test_integer_text_follows_the_one_rule(text: str, issue: str) -> None:
    with pytest.raises(InvalidValueError) as raised:
        parse_integer(text, "x", 0, 100)
    assert raised.value.issue == issue


@given(st.integers(-(10**18) + 1, 10**18 - 1))
def test_every_well_formed_integer_reads_back(value: int) -> None:
    assert parse_integer(str(value), "x") == value


@pytest.mark.parametrize("value", ["\u00b2", "\u0663", "+3", "3.0", "-1"])
def test_a_malformed_integer_setting_names_its_variable(value: str) -> None:
    with pytest.raises(OperatorError) as raised:
        RuntimeSettings.from_env({"PIPELINE_MAX_ATTEMPTS": value})
    assert "PIPELINE_MAX_ATTEMPTS" in raised.value.sentence and raised.value.code == "CONFIGURATION_ERROR"


@pytest.mark.parametrize("value", ["\u0661.\u0665", "1e1", "\u00b2", "0x1", " "])
def test_a_malformed_seconds_setting_names_its_variable(value: str) -> None:
    if value.strip(" "):
        with pytest.raises(OperatorError) as raised:
            RuntimeSettings.from_env({"PIPELINE_POLL_SECONDS": value})
        assert "PIPELINE_POLL_SECONDS" in raised.value.sentence
    assert RuntimeSettings.from_env({"PIPELINE_POLL_SECONDS": "0.5"}).tunables.pipeline_poll_seconds == 0.5
