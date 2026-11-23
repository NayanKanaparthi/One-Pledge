"""Digests of what a stage produced, kept in its audit entry (SPEC_DECISIONS 107 a).

When a stage completes, its `TAPE_STAGE_ADVANCED` entry carries the SHA-256 of the canonical JSON
of the rows it produced, in a fixed order, with fixed columns; `LOAN_RESUBMITTED` carries the
digest of its request. Deep verification recomputes each from the tables and compares: a
difference is `DIGEST_MISMATCH` with the table, the submission (or the request) and the stage.

- CLEANED: `items_sha256` (`submission_items`), `issues_sha256` (`cleaning_issues`);
- CHECKED: `checks_sha256` (`local_checks`);
- SUBMITTED: `tokens_sha256` (`loan_tokens` of the loans of its requests, of the key versions of
  those requests), `requests_sha256` (`registry_requests` with their items and the SHA-256 of
  each stored response body).

The rows are read by `HistoryReader.stage_rows`, each a tuple of JSON cells (`cell`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum, StrEnum
from types import MappingProxyType

from onepledge_bank_agent.domain.canonical_json import JsonValue, canonical_json, sha256_hex
from onepledge_bank_agent.domain.enums import TapeStatus
from onepledge_bank_agent.domain.integrity import render_time

type DigestRow = tuple[JsonValue, ...]


class StagePart(StrEnum):
    """The rows a stage digest covers, by its detail key."""

    ITEMS = "items_sha256"
    ISSUES = "issues_sha256"
    CHECKS = "checks_sha256"
    TOKENS = "tokens_sha256"
    REQUESTS = "requests_sha256"


STAGE_PARTS: Mapping[TapeStatus, tuple[StagePart, ...]] = MappingProxyType(
    {
        TapeStatus.CLEANED: (StagePart.ITEMS, StagePart.ISSUES),
        TapeStatus.CHECKED: (StagePart.CHECKS,),
        TapeStatus.SUBMITTED: (StagePart.TOKENS, StagePart.REQUESTS),
    }
)
"""The digests of the entry for the move to each status."""

PART_TABLES: Mapping[StagePart, str] = MappingProxyType(
    {
        StagePart.ITEMS: "submission_items",
        StagePart.ISSUES: "cleaning_issues",
        StagePart.CHECKS: "local_checks",
        StagePart.TOKENS: "loan_tokens",
        StagePart.REQUESTS: "registry_requests",
    }
)
"""The table a difference is reported on."""


def cell(value: object) -> JsonValue:
    """One column as a canonical JSON cell: ids and times as text, enums by value, documents as they are."""
    found: JsonValue
    if value is None or isinstance(value, bool | int | str):
        found = value
    elif isinstance(value, Enum):
        found = cell(value.value)
    elif isinstance(value, datetime):
        found = render_time(value)
    elif isinstance(value, date):
        found = value.isoformat()
    elif isinstance(value, Mapping):
        found = {str(key): cell(item) for key, item in value.items()}
    elif isinstance(value, Sequence):
        found = [cell(item) for item in value]
    else:  # a UUID, or a value object that is its text
        found = str(value)
    return found


def row(*values: object) -> DigestRow:
    """A digest row of these columns."""
    return tuple(cell(value) for value in values)


def body_sha256(body: object) -> str | None:
    """The SHA-256 of the canonical JSON of a stored response body, or None without one."""
    return None if body is None else sha256_hex(canonical_json(cell(body)))


def rows_sha256(rows: Sequence[DigestRow]) -> str:
    """The digest of rows given in their fixed order."""
    return sha256_hex(canonical_json([list(item) for item in rows]))
