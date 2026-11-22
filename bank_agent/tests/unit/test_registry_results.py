"""How registry answers are checked and applied (SPEC 13.4, 13.5, 14.5)."""

from __future__ import annotations

import itertools
import uuid

import pytest

from onepledge_bank_agent.domain.enums import (
    LoanState,
    MatchBasis,
    MatchKind,
    RegistryItemResult,
    RequestOrigin,
    TapeKind,
)
from onepledge_bank_agent.domain.registry_records import ItemOutcome
from onepledge_bank_agent.domain.registry_results import (
    UNKNOWN_RESULT,
    LoanMove,
    SentItem,
    StoredResult,
    answer_matches,
    loan_move,
    stored_result,
)

pytestmark = pytest.mark.unit

REFS = [uuid.UUID(int=n, version=4) for n in (1, 2)]
SENT = [SentItem(REFS[0], "a" * 64), SentItem(REFS[1], "b" * 64)]


def outcome(
    ref: uuid.UUID, token: str, result: str, reason: str | None = None, case: bool = True
) -> ItemOutcome:
    conflict = result == "CONFLICT" and case
    return ItemOutcome(
        ref, token, result, None, False,
        MatchKind.EXACT if conflict else None, MatchBasis.ASSET if conflict else None, (),
        uuid.uuid4() if conflict else None, reason,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("kind", "result", "reason", "stored"),
    [
        (TapeKind.PLEDGE, "LOCKED", None, StoredResult(RegistryItemResult.LOCKED, None)),
        (TapeKind.BACK_BOOK, "CONFLICT", None, StoredResult(RegistryItemResult.CONFLICT, None)),
        (TapeKind.RELEASE, "RELEASED", None, StoredResult(RegistryItemResult.RELEASED, None)),
        (TapeKind.RELEASE, "REJECTED", "NOT_HOLDER", StoredResult(RegistryItemResult.REJECTED, "NOT_HOLDER")),
        (TapeKind.PLEDGE, "REJECTED", None, StoredResult(RegistryItemResult.REJECTED, UNKNOWN_RESULT)),
        (TapeKind.PLEDGE, "RELEASED", None, StoredResult(RegistryItemResult.REJECTED, UNKNOWN_RESULT)),
        (TapeKind.RELEASE, "LOCKED", None, StoredResult(RegistryItemResult.REJECTED, UNKNOWN_RESULT)),
        (TapeKind.PLEDGE, "SUSPENDED", None, StoredResult(RegistryItemResult.REJECTED, UNKNOWN_RESULT)),
    ],
)
def test_a_result_is_stored_as_known_or_as_rejected_with_unknown_result(
    kind: TapeKind, result: str, reason: str | None, stored: StoredResult
) -> None:
    assert stored_result(kind, outcome(REFS[0], "a" * 64, result, reason)) == stored


MOVES = {
    (RequestOrigin.TAPE, RegistryItemResult.LOCKED, LoanState.PENDING): LoanMove.LOCK,
    (RequestOrigin.RESUBMIT, RegistryItemResult.LOCKED, LoanState.CONFLICT): LoanMove.LOCK_AFTER_CLEARANCE,
    (RequestOrigin.RESUBMIT, RegistryItemResult.LOCKED, LoanState.PENDING): LoanMove.LOCK,
    (RequestOrigin.TAPE, RegistryItemResult.CONFLICT, LoanState.PENDING): LoanMove.MARK_CONFLICT,
    (RequestOrigin.RESUBMIT, RegistryItemResult.CONFLICT, LoanState.PENDING): LoanMove.MARK_CONFLICT,
    (RequestOrigin.TAPE, RegistryItemResult.RELEASED, LoanState.LOCKED): LoanMove.RELEASE,
    (RequestOrigin.RESUBMIT, RegistryItemResult.RELEASED, LoanState.LOCKED): LoanMove.RELEASE,
}


def test_every_result_from_every_state_moves_the_loan_only_where_the_spec_says() -> None:
    for origin, result, state in itertools.product(RequestOrigin, RegistryItemResult, LoanState):
        assert loan_move(origin, result, state) is MOVES.get((origin, result, state), LoanMove.NONE)


def test_an_answer_must_hold_one_result_per_item_sent_with_the_same_asset_codes() -> None:
    good = [outcome(REFS[0], "a" * 64, "LOCKED"), outcome(REFS[1], "b" * 64, "CONFLICT")]
    assert answer_matches(SENT, good)
    assert answer_matches(SENT, list(reversed(good)))
    bad = [
        good[:1],
        [*good, outcome(uuid.uuid4(), "c" * 64, "LOCKED")],
        [good[0], outcome(REFS[1], "c" * 64, "LOCKED")],
        [good[0], outcome(uuid.uuid4(), "b" * 64, "LOCKED")],
        [good[0], good[0]],
        [good[0], outcome(REFS[1], "b" * 64, "CONFLICT", case=False)],
    ]
    for answer in bad:
        assert not answer_matches(SENT, answer)
