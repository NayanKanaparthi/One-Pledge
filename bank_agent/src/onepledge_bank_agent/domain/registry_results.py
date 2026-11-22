"""How a registry answer is checked and applied to loans (SPEC 13.4, 13.5, 14.5), as pure rules.

An answer is accepted only when it holds exactly one result per item sent, every
`item_ref` is one the bank sent, every asset code equals the one sent with it, and every
CONFLICT names its case and match. A result value the bank does not know, or one that
makes no sense for the request kind, is stored as REJECTED with reason UNKNOWN_RESULT
(the contract's rule for additive evolution).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from onepledge_bank_agent.domain.enums import LoanState, RegistryItemResult, RequestOrigin, TapeKind
from onepledge_bank_agent.domain.registry_records import ItemOutcome

UNKNOWN_RESULT = "UNKNOWN_RESULT"
_LOCK_RESULTS = frozenset(
    {RegistryItemResult.LOCKED, RegistryItemResult.CONFLICT, RegistryItemResult.REJECTED}
)
VALID_RESULTS: Mapping[TapeKind, frozenset[RegistryItemResult]] = MappingProxyType(
    {
        TapeKind.BACK_BOOK: _LOCK_RESULTS,
        TapeKind.PLEDGE: _LOCK_RESULTS,
        TapeKind.RELEASE: frozenset({RegistryItemResult.RELEASED, RegistryItemResult.REJECTED}),
    }
)


class LoanMove(StrEnum):
    """What a stored result does to the loan of its item."""

    LOCK = "LOCK"
    LOCK_AFTER_CLEARANCE = "LOCK_AFTER_CLEARANCE"
    MARK_CONFLICT = "MARK_CONFLICT"
    RELEASE = "RELEASE"
    NONE = "NONE"


@dataclass(frozen=True)
class SentItem:
    """One item as the bank sent it."""

    item_ref: uuid.UUID
    asset_token: str


@dataclass(frozen=True)
class StoredResult:
    """The result as the bank stores it: a known value, and a reason for a rejection."""

    result: RegistryItemResult
    reason: str | None


def stored_result(kind: TapeKind, outcome: ItemOutcome) -> StoredResult:
    """The known result of an item, or REJECTED with UNKNOWN_RESULT."""
    try:
        result = RegistryItemResult(outcome.result)
    except ValueError:
        return StoredResult(RegistryItemResult.REJECTED, UNKNOWN_RESULT)
    if result not in VALID_RESULTS[kind]:
        return StoredResult(RegistryItemResult.REJECTED, UNKNOWN_RESULT)
    if result is RegistryItemResult.REJECTED:
        return StoredResult(result, outcome.reason or UNKNOWN_RESULT)
    return StoredResult(result, None)


def loan_move(origin: RequestOrigin, result: RegistryItemResult, state: LoanState) -> LoanMove:
    """The loan transition a result causes from the loan's current state (none when it is not applicable).

    A result that finds its loan already in the target state, for example a replayed
    decision after the same car was locked by a later tape, changes nothing.
    """
    if result is RegistryItemResult.LOCKED:
        if origin is RequestOrigin.RESUBMIT and state is LoanState.CONFLICT:
            return LoanMove.LOCK_AFTER_CLEARANCE
        return LoanMove.LOCK if state is LoanState.PENDING else LoanMove.NONE
    if result is RegistryItemResult.CONFLICT:
        return LoanMove.MARK_CONFLICT if state is LoanState.PENDING else LoanMove.NONE
    if result is RegistryItemResult.RELEASED:
        return LoanMove.RELEASE if state is LoanState.LOCKED else LoanMove.NONE
    return LoanMove.NONE


def answer_matches(sent: Sequence[SentItem], results: Sequence[ItemOutcome]) -> bool:
    """One result per item sent, each for a known `item_ref` with the same asset code; conflicts complete."""
    if len(results) != len(sent):
        return False
    tokens = {item.item_ref: item.asset_token for item in sent}
    seen: set[uuid.UUID] = set()
    for outcome in results:
        if tokens.get(outcome.item_ref) != outcome.asset_token or outcome.item_ref in seen:
            return False
        seen.add(outcome.item_ref)
        if outcome.result == RegistryItemResult.CONFLICT.value and (
            outcome.case_id is None or outcome.match_kind is None or outcome.match_basis is None
        ):
            return False
    return True
