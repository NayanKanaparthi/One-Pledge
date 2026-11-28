"""The values of one evaluate call: the request, the result and the log record.

The log record has no field that could hold an element (SPEC 4.1, decision 12).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from onepledge_key_holder.domain.identity import HolderId, KeyVersion


@dataclass(frozen=True)
class EvaluationRequest:
    """A well formed `EvaluateRequest`: its id, the pinned version if any, the base64 elements."""

    request_id: str
    key_version: KeyVersion | None
    elements: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationResult:
    """`EvaluateResponse`: the elements in request order and the one version used for all of them."""

    request_id: str
    holder_id: HolderId
    key_version: KeyVersion
    elements: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationRecord:
    """One row of `evaluation_log`: who asked, the request id, how many elements, when."""

    eval_id: uuid.UUID
    member_id: str
    request_id: str
    item_count: int
    at: datetime
