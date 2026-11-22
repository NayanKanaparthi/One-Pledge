"""Plain language explanations of alerts (SPEC 14.3): `AlertFacts`, `Explanation`, `TemplateAlertExplainer`.

`AlertFacts` holds only the role, the match kind and basis and the kinds that matched
or did not. It carries no loan value, so no explainer, template or model, can leak one.
The template explainer is deterministic: the same facts always give the same text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from onepledge_bank_agent.domain.enums import AlertRole, ExplanationSource, MatchBasis, MatchKind, TokenKind

OPENINGS: Mapping[tuple[AlertRole, MatchBasis], str] = MappingProxyType(
    {
        (AlertRole.CLAIMANT, MatchBasis.ASSET): "Another registry member already holds a lock on this "
        "vehicle, so this pledge was not locked.",
        (AlertRole.CLAIMANT, MatchBasis.FIELDS): "This vehicle code is not locked by anyone, but another "
        "registry member holds a lock on a loan that agrees with this one on several fields, so this "
        "pledge was held and not locked.",
        (AlertRole.HOLDER, MatchBasis.ASSET): "Another registry member tried to pledge the vehicle that "
        "this bank holds a lock on. This bank's lock is unchanged.",
        (AlertRole.HOLDER, MatchBasis.FIELDS): "Another registry member tried to pledge a loan that agrees "
        "with this locked loan on several fields but carries a different vehicle code. This bank's lock "
        "is unchanged.",
    }
)
EVERY_FIELD = (
    "The other pledge agrees with this loan on every compared field: vehicle, borrower, original amount "
    "and origination date."
)
READING_EXACT = "This is the pattern of the same loan pledged twice."
READING_LIKELY: Mapping[MatchBasis, str] = MappingProxyType(
    {
        MatchBasis.ASSET: "This is the pattern of a copy of the loan with some details changed.",
        MatchBasis.FIELDS: "This is the pattern of a copy of the loan with the vehicle identifier changed. "
        "It can also be a coincidence, for example one borrower financing two cars on the same day for "
        "the same amount.",
    }
)
CLOSING = (
    "The registry does not reveal who the other member is. Review the loan with the lender and record "
    "the bank's position on the case."
)
VEHICLE, BORROWER, AMOUNT, DATE = "the vehicle", "the borrower", "the original amount", "the origination date"
AMOUNT_GRADES = (
    (TokenKind.AMOUNT_EXACT, "the original amount (exactly)"),
    (TokenKind.AMOUNT_100, "the original amount (to the nearest 100 dollars)"),
    (TokenKind.AMOUNT_1000, "the original amount (to the nearest 1,000 dollars)"),
)
DATE_GRADES = (
    (TokenKind.DATE_DAY, "the origination date (same day)"),
    (TokenKind.DATE_MONTH, "the origination date (same month)"),
)
EXACT_AMOUNT_DIFFERS = "the exact original amount"
EXACT_DAY_DIFFERS = "the exact origination day"


@dataclass(frozen=True)
class AlertFacts:
    """What an explanation may rest on: the registry's match facts and nothing about the loan."""

    role: AlertRole
    match_kind: MatchKind
    match_basis: MatchBasis
    kinds_matched: tuple[TokenKind, ...]
    kinds_not_matched: tuple[TokenKind, ...]


@dataclass(frozen=True)
class Explanation:
    """The text shown with an alert and who wrote it."""

    text: str
    source: ExplanationSource


def join_plain(parts: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _graded(
    matched: frozenset[TokenKind], grades: Sequence[tuple[TokenKind, str]], field: str, finer: str
) -> tuple[str | None, str | None]:
    """The agreement phrase for the finest matched grade, and what still differs."""
    for index, (kind, phrase) in enumerate(grades):
        if kind in matched:
            return phrase, (finer if index > 0 else None)
    return None, field


def agreement(kinds_matched: Sequence[TokenKind]) -> tuple[list[str], list[str]]:
    """The fields the other pledge agrees on and the fields it differs on, in a fixed order."""
    matched = frozenset(kinds_matched)
    agrees: list[str] = []
    differs: list[str] = []
    vehicle = VEHICLE if matched & {TokenKind.ASSET, TokenKind.VIN} else None
    borrower = BORROWER if TokenKind.BORROWER in matched else None
    for phrase, missing in (
        (vehicle, None if vehicle else VEHICLE),
        (borrower, None if borrower else BORROWER),
        _graded(matched, AMOUNT_GRADES, AMOUNT, EXACT_AMOUNT_DIFFERS),
        _graded(matched, DATE_GRADES, DATE, EXACT_DAY_DIFFERS),
    ):
        if phrase is not None:
            agrees.append(phrase)
        if missing is not None:
            differs.append(missing)
    return agrees, differs


class TemplateAlertExplainer:
    """The deterministic explainer of layer 1 (the `AlertExplainer` port). It never calls out."""

    def explain(self, facts: AlertFacts) -> Explanation:
        """Opening by role and basis, agreement, reading, closing (SPEC 14.3)."""
        sentences = [OPENINGS[(facts.role, facts.match_basis)]]
        if facts.match_kind is MatchKind.EXACT:
            sentences.extend((EVERY_FIELD, READING_EXACT))
        else:
            agrees, differs = agreement(facts.kinds_matched)
            if agrees:
                sentences.append(f"The other pledge agrees with this loan on: {join_plain(agrees)}.")
            if differs:
                sentences.append(f"It differs on: {join_plain(differs)}.")
            sentences.append(READING_LIKELY[facts.match_basis])
        sentences.append(CLOSING)
        return Explanation(" ".join(sentences), ExplanationSource.TEMPLATE)
