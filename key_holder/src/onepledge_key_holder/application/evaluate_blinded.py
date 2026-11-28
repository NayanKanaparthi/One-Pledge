"""`EvaluateBlinded`: `POST /v1/evaluate` (SPEC 4.2, `contracts/key_holder.yaml`).

The order is the layer 1 double's: resolve the key version, evaluate every element, count
the elements against the member's daily cap, append the log row. A request with any invalid
element is refused as a whole before the cap or the log is touched, so it leaves no row.
The holder does not unblind and does not hash a point into a token; the bank does both.
"""

from __future__ import annotations

from dataclasses import dataclass

from onepledge_key_holder.application.ports import Clock, EvaluationJournal, Evaluator, IdSource, Keyring
from onepledge_key_holder.domain.daily_window import seconds_until_reset, window_start
from onepledge_key_holder.domain.elements import IDENTITY, decode_element, encode_element
from onepledge_key_holder.domain.errors import (
    InvalidElementError,
    KeyVersionUnavailableError,
    RateLimitedError,
)
from onepledge_key_holder.domain.evaluation import EvaluationRecord, EvaluationRequest, EvaluationResult
from onepledge_key_holder.domain.identity import HolderId, KeyVersion


@dataclass(frozen=True)
class HolderKeys:
    """Who this holder is, the version it uses when none is pinned, and its keyring."""

    holder_id: HolderId
    active_version: KeyVersion
    keyring: Keyring


def _evaluate_one(evaluator: Evaluator, encoded: str) -> str | None:
    """The evaluated element, or None for an element or a result that is not a valid point."""
    point = decode_element(encoded)
    if point is None:
        return None
    result = evaluator.evaluate(point)
    if result is None or result == IDENTITY:
        return None
    return encode_element(result)


class EvaluateBlinded:
    """Evaluates every element of a request with one key version, or refuses the whole request."""

    def __init__(
        self,
        keys: HolderKeys,
        journal: EvaluationJournal,
        clock: Clock,
        ids: IdSource,
        daily_element_cap: int,
    ) -> None:
        self._keys = keys
        self._journal = journal
        self._clock = clock
        self._ids = ids
        self._cap = daily_element_cap

    def run(self, member_id: str, request: EvaluationRequest) -> EvaluationResult:
        """The evaluated elements in request order, recorded as one log row for `member_id`."""
        version = request.key_version or self._keys.active_version
        evaluator = self._keys.keyring.evaluator(version)
        if evaluator is None:
            raise KeyVersionUnavailableError
        evaluated: list[str] = []
        failing: list[int] = []
        for index, encoded in enumerate(request.elements):
            result = _evaluate_one(evaluator, encoded)
            if result is None:
                failing.append(index)
            else:
                evaluated.append(result)
        if failing:
            raise InvalidElementError(tuple(failing))
        now = self._clock.now()
        record = EvaluationRecord(self._ids.new_id(), member_id, request.request_id, len(evaluated), now)
        if not self._journal.accept(record, window_start(now), self._cap):
            raise RateLimitedError(seconds_until_reset(now))
        return EvaluationResult(request.request_id, self._keys.holder_id, version, tuple(evaluated))
