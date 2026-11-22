"""Idempotency keys of registry requests (SPEC 13.4, 14.5).

```
items_digest    = sha256("\\n".join(asset_token of each item in position order)).hexdigest()
idempotency_key = sha256("onepledge:idem:v1|" + member_id + "|" + submission_id + "|" + origin + "|"
                         + kind + "|" + batch_part + "|" + items_digest)
```

`batch_part` is the batch index for a tape request and `"case:" + ",".join(sorted(case ids))`
for a resubmission, so repeating a resubmission for the same cleared cases replays the
registry's first decision. The key is computed once, when the request is planned, and
stored; it is never recomputed.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from onepledge_bank_agent.domain.enums import RequestOrigin, TapeKind
from onepledge_bank_agent.domain.errors import InvalidValueError

KEY_PREFIX = "onepledge:idem:v1"
CASE_PREFIX = "case:"


def items_digest(asset_tokens: Iterable[str]) -> str:
    """SHA-256 of the asset tokens in position order, joined by newlines."""
    return hashlib.sha256("\n".join(asset_tokens).encode("utf-8")).hexdigest()


def tape_batch_part(batch_index: int) -> str:
    """The batch part of a tape request: its batch index."""
    if isinstance(batch_index, bool) or batch_index < 0:
        raise InvalidValueError("batch_index", "OUT_OF_RANGE")
    return str(batch_index)


def resubmission_batch_part(case_ids: Iterable[uuid.UUID]) -> str:
    """The batch part of a resubmission: the cleared case ids, sorted and comma separated."""
    ordered = sorted(str(case_id) for case_id in case_ids)
    if not ordered:
        raise InvalidValueError("case_ids", "TOO_FEW_ITEMS")
    return CASE_PREFIX + ",".join(ordered)


@dataclass(frozen=True)
class IdempotencyInputs:
    """Everything an idempotency key is derived from."""

    member_id: uuid.UUID
    submission_id: uuid.UUID
    origin: RequestOrigin
    kind: TapeKind
    batch_part: str
    items_digest: str

    def key(self) -> str:
        """The 64 hex character idempotency key."""
        text = "|".join(
            (
                KEY_PREFIX,
                str(self.member_id),
                str(self.submission_id),
                self.origin.value,
                self.kind.value,
                self.batch_part,
                self.items_digest,
            )
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
