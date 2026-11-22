"""Planning registry requests and building their bodies (SPEC 13.4).

Eligible loans are ordered by row number and split into batches of
`SUBMIT_BATCH_ITEMS`. Each batch becomes one `registry_requests` row with its items,
fresh item references, positions, the key version of the codes and the idempotency key,
all stored when the plan is made. A request body is built only from those stored rows,
so every attempt sends exactly the same body under the same key.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from onepledge_bank_agent.application.evidence_ports import ItemRecord, PlannedItem
from onepledge_bank_agent.application.ports import IdGenerator
from onepledge_bank_agent.domain.entities import RegistryRequest
from onepledge_bank_agent.domain.enums import RequestOrigin, TapeKind, TokenKind
from onepledge_bank_agent.domain.idempotency import IdempotencyInputs, items_digest
from onepledge_bank_agent.domain.registry_records import RegistryItem, RegistrySubmission
from onepledge_bank_agent.domain.values import KeyVersion, Sha256Hex, Token

FIELD_KINDS: tuple[TokenKind, ...] = tuple(kind for kind in TokenKind if kind is not TokenKind.ASSET)


@dataclass(frozen=True)
class Candidate:
    """A loan ready to be sent: its row, its ASSET code and the key version of its codes."""

    row_number: int
    loan_id: uuid.UUID
    asset_token: str
    key_version: KeyVersion


@dataclass(frozen=True)
class RequestScope:
    """What every request of one plan shares: who sends it, for which submission, why, and what kind."""

    member_id: uuid.UUID
    submission_id: uuid.UUID
    origin: RequestOrigin
    kind: TapeKind


@dataclass(frozen=True)
class PlannedRequest:
    """A request row and its items, ready to be inserted."""

    request: RegistryRequest
    items: tuple[PlannedItem, ...]


def planned_request(
    scope: RequestScope,
    batch: Sequence[Candidate],
    batch_index: int,
    batch_part: str,
    ids: IdGenerator,
) -> PlannedRequest:
    """One request for a batch: items in position order, the key derived from their ASSET codes."""
    digest = items_digest(candidate.asset_token for candidate in batch)
    key = IdempotencyInputs(
        scope.member_id, scope.submission_id, scope.origin, scope.kind, batch_part, digest
    ).key()
    request = RegistryRequest(
        request_id=ids.new_id(),
        submission_id=scope.submission_id,
        origin=scope.origin,
        batch_index=batch_index,
        idempotency_key=Sha256Hex(key),
        kind=scope.kind,
        key_version=batch[0].key_version,
        item_count=len(batch),
        attempts=0,
        response_status=None,
        certificate_id=None,
    )
    items = tuple(
        PlannedItem(ids.new_id(), position, candidate.loan_id) for position, candidate in enumerate(batch)
    )
    return PlannedRequest(request, items)


def batches(candidates: Sequence[Candidate], size: int) -> list[list[Candidate]]:
    """Row order, then consecutive batches of at most `size`, one key version per batch."""
    ordered = sorted(candidates, key=lambda candidate: candidate.row_number)
    result: list[list[Candidate]] = []
    for candidate in ordered:
        current = result[-1] if result else None
        if current is None or len(current) >= size or current[0].key_version != candidate.key_version:
            result.append([candidate])
        else:
            current.append(candidate)
    return result


def submission_body(
    request: RegistryRequest, items: Sequence[ItemRecord], tokens: Mapping[uuid.UUID, Sequence[Token]]
) -> RegistrySubmission:
    """The body of one request, from its stored items and the stored codes at its key version."""
    entries = []
    for item in sorted(items, key=lambda record: record.position):
        codes = {t.kind: t.value.value for t in tokens[item.loan_id] if t.key_version == request.key_version}
        fields = None if request.kind is TapeKind.RELEASE else {kind: codes[kind] for kind in FIELD_KINDS}
        entries.append(RegistryItem(item.item_ref, codes[TokenKind.ASSET], fields))
    return RegistrySubmission(request.kind, str(request.key_version), tuple(entries))
