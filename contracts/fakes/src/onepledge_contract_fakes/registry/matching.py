"""Decision rules A, B and C of `contracts/registry.yaml` (TEST DOUBLE).

- Rule A: the caller already holds the active lock on the asset code.
- Rule B: another member holds it. EXACT when BORROWER, AMOUNT_EXACT and DATE_DAY
  all equal the lock's codes, otherwise LIKELY.
- Rule C: the asset code is free and another member's active lock has the same
  BORROWER code and one of the pairs (AMOUNT_EXACT, DATE_DAY), (AMOUNT_EXACT,
  DATE_MONTH), (AMOUNT_100, DATE_DAY). Most equal kinds wins, ties go to the oldest
  lock. A pair cleared as a data error is exempt.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from onepledge_contract_fakes.registry.locks import Lock, LockTable
from onepledge_contract_fakes.registry.model import TOKEN_KINDS, MatchBasis, MatchKind

EXACT_KINDS: tuple[str, ...] = ("BORROWER", "AMOUNT_EXACT", "DATE_DAY")
RULE_C_PAIRS: tuple[tuple[str, str], ...] = (
    ("AMOUNT_EXACT", "DATE_DAY"),
    ("AMOUNT_EXACT", "DATE_MONTH"),
    ("AMOUNT_100", "DATE_DAY"),
)


@dataclass(frozen=True)
class Match:
    """A conflicting item and the lock it matched."""

    lock: Lock
    basis: MatchBasis
    kind: MatchKind
    kinds_matched: tuple[str, ...]


@dataclass(frozen=True)
class AlreadyHeld:
    """Rule A."""

    lock: Lock


@dataclass(frozen=True)
class Free:
    """No rule matched: the item gets a new lock."""


type Decision = AlreadyHeld | Match | Free


def kinds_matched(asset_token: str, field_tokens: Mapping[str, str], lock: Lock) -> tuple[str, ...]:
    """Every kind whose code equals the lock's code, in the fixed kind order."""
    matched = []
    for kind in TOKEN_KINDS:
        value = asset_token if kind == "ASSET" else field_tokens[kind]
        if value == lock.token(kind):
            matched.append(kind)
    return tuple(matched)


class MatchingRules:
    """Applies rules A, B and C to one lock request against the current locks."""

    def __init__(self, locks: LockTable, is_exempt: Callable[[str, str], bool]) -> None:
        self._locks = locks
        self._is_exempt = is_exempt

    def decide(self, member_id: str, asset_token: str, field_tokens: Mapping[str, str]) -> Decision:
        """The decision for one BACK_BOOK or PLEDGE item."""
        holder = self._locks.holder(asset_token)
        if holder is not None:
            if holder.member_id == member_id:
                return AlreadyHeld(holder)
            matched = kinds_matched(asset_token, field_tokens, holder)
            exact = all(kind in matched for kind in EXACT_KINDS)
            return Match(holder, MatchBasis.ASSET, MatchKind.EXACT if exact else MatchKind.LIKELY, matched)
        return self._field_agreement(member_id, asset_token, field_tokens)

    def _field_agreement(self, member_id: str, asset_token: str, field_tokens: Mapping[str, str]) -> Decision:
        best: Match | None = None
        for lock in self._locks.with_borrower(field_tokens["BORROWER"]):
            if lock.member_id == member_id or self._is_exempt(asset_token, lock.lock_id):
                continue
            matched = kinds_matched(asset_token, field_tokens, lock)
            if not any(first in matched and second in matched for first, second in RULE_C_PAIRS):
                continue
            if best is None or len(matched) > len(best.kinds_matched):
                best = Match(lock, MatchBasis.FIELDS, MatchKind.LIKELY, matched)
        return best if best is not None else Free()
