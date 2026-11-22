"""`TemplateAlertExplainer` (SPEC 14.3): every role, basis and kind combination; no loan value can appear."""

from __future__ import annotations

import itertools
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from onepledge_bank_agent.domain.enums import AlertRole, ExplanationSource, MatchBasis, MatchKind, TokenKind
from onepledge_bank_agent.domain.explanation import (
    CLOSING,
    OPENINGS,
    AlertFacts,
    TemplateAlertExplainer,
    agreement,
    join_plain,
)

pytestmark = pytest.mark.unit

EXPLAINER = TemplateAlertExplainer()
LONG_DIGIT_RUN = re.compile(r"\d{6,}")
KIND_SETS = st.sets(st.sampled_from(list(TokenKind)), min_size=1)


def facts(role: AlertRole, kind: MatchKind, basis: MatchBasis, matched: set[TokenKind]) -> AlertFacts:
    ordered = tuple(k for k in TokenKind if k in matched)
    return AlertFacts(role, kind, basis, ordered, tuple(k for k in TokenKind if k not in matched))


COMBINATIONS = list(itertools.product(AlertRole, MatchKind, MatchBasis))


@pytest.mark.parametrize(("role", "kind", "basis"), COMBINATIONS, ids=["-".join(c) for c in COMBINATIONS])
def test_every_role_kind_and_basis_has_its_opening_reading_and_closing(
    role: AlertRole, kind: MatchKind, basis: MatchBasis
) -> None:
    matched = set(TokenKind) if kind is MatchKind.EXACT else {TokenKind.BORROWER, TokenKind.AMOUNT_100}
    explanation = EXPLAINER.explain(facts(role, kind, basis, matched))
    assert explanation.source is ExplanationSource.TEMPLATE
    assert explanation.text.startswith(OPENINGS[(role, basis)])
    assert explanation.text.endswith(CLOSING)
    if kind is MatchKind.EXACT:
        assert (
            "every compared field: vehicle, borrower, original amount and origination date."
            in explanation.text
        )
        assert "This is the pattern of the same loan pledged twice." in explanation.text
    else:
        assert "pattern of a copy of the loan" in explanation.text
    assert LONG_DIGIT_RUN.search(explanation.text) is None


def test_a_likely_fields_match_reads_as_the_spec_sentences() -> None:
    matched = {TokenKind.BORROWER, TokenKind.AMOUNT_EXACT, TokenKind.AMOUNT_100, TokenKind.AMOUNT_1000}
    text = EXPLAINER.explain(facts(AlertRole.CLAIMANT, MatchKind.LIKELY, MatchBasis.FIELDS, matched)).text
    assert text == (
        "This vehicle code is not locked by anyone, but another registry member holds a lock on a loan that "
        "agrees with this one on several fields, so this pledge was held and not locked. "
        "The other pledge agrees with this loan on: the borrower and the original amount (exactly). "
        "It differs on: the vehicle and the origination date. "
        "This is the pattern of a copy of the loan with the vehicle identifier changed. It can also be a "
        "coincidence, for example one borrower financing two cars on the same day for the same amount. "
        "The registry does not reveal who the other member is. Review the loan with the lender and record "
        "the bank's position on the case."
    )


AMOUNT_1000 = "the original amount (to the nearest 1,000 dollars)"
AMOUNT_100 = "the original amount (to the nearest 100 dollars)"
GRADES = [
    ({TokenKind.ASSET}, ["the vehicle"], ["the borrower", "the original amount", "the origination date"]),
    (
        {TokenKind.VIN, TokenKind.BORROWER},
        ["the vehicle", "the borrower"],
        ["the original amount", "the origination date"],
    ),
    (
        {TokenKind.BORROWER, TokenKind.AMOUNT_1000, TokenKind.DATE_MONTH},
        ["the borrower", AMOUNT_1000, "the origination date (same month)"],
        ["the vehicle", "the exact original amount", "the exact origination day"],
    ),
    (
        {TokenKind.BORROWER, TokenKind.AMOUNT_100, TokenKind.AMOUNT_1000, TokenKind.DATE_DAY},
        ["the borrower", AMOUNT_100, "the origination date (same day)"],
        ["the vehicle", "the exact original amount"],
    ),
]


@pytest.mark.parametrize(("matched", "agrees", "differs"), GRADES)
def test_the_finest_matched_grade_is_named_and_what_is_finer_differs(
    matched: set[TokenKind], agrees: list[str], differs: list[str]
) -> None:
    assert agreement([k for k in TokenKind if k in matched]) == (agrees, differs)


@given(st.sampled_from(list(AlertRole)), st.sampled_from(list(MatchBasis)), KIND_SETS)
def test_any_facts_give_a_plain_deterministic_text_without_long_digit_runs(
    role: AlertRole, basis: MatchBasis, matched: set[TokenKind]
) -> None:
    likely = facts(role, MatchKind.LIKELY, basis, matched)
    first, second = EXPLAINER.explain(likely), EXPLAINER.explain(likely)
    assert first == second
    assert LONG_DIGIT_RUN.search(first.text) is None
    assert first.text.count("The registry does not reveal who the other member is.") == 1
    assert all(sentence[:1].isupper() for sentence in re.split(r"(?<=\.) ", first.text))


def test_plain_lists_join_with_commas_and_a_final_and() -> None:
    assert [join_plain(parts) for parts in ([], ["a"], ["a", "b"], ["a", "b", "c"])] == [
        "",
        "a",
        "a and b",
        "a, b and c",
    ]
