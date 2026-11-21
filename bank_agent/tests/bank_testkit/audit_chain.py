"""An independent check of the bank audit chain (SPEC 17.1), written from the SPEC with no product code."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from bank_testkit.database import BankDatabase

QUERY = (
    "SELECT audit_id, seq, at, actor_type, actor_user_id, action, object_type, object_id, outcome, "
    "request_id, detail, prev_hash, entry_hash FROM audit_log ORDER BY seq"
)


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def first_broken_seq(database: BankDatabase) -> int | None:
    """The first seq whose number, link or hash is wrong, or None when the whole chain holds."""
    with database.connect("admin") as connection:
        rows = connection.execute(QUERY).fetchall()
    previous = "0" * 64
    for expected, row in enumerate(rows, start=1):
        (
            audit_id,
            seq,
            at,
            actor_type,
            actor,
            action,
            object_type,
            object_id,
            outcome,
            request_id,
            detail,
            prev,
            stored,
        ) = row
        assert isinstance(at, datetime)
        body = {
            "action": action, "actor_type": actor_type, "actor_user_id": _text(actor),
            "at": at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "audit_id": str(audit_id),
            "detail": detail,
            "object_id": _text(object_id), "object_type": object_type, "outcome": outcome, "prev_hash": prev,
            "request_id": request_id, "seq": seq,
        }  # fmt: skip
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        if seq != expected or prev != previous or hashlib.sha256(canonical).hexdigest() != stored:
            return expected
        previous = str(stored)
    return None
